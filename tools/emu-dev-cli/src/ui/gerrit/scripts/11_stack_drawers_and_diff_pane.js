  function openPgStackSequenceDialog(stackId) {
    const activeStack = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!activeStack || !Array.isArray(activeStack.commits) || activeStack.commits.length <= 1) {
      showToast("This change is not part of a multi-commit stack", 2500);
      return;
    }
    state.stackSequenceDialogOpen = true;
    renderPgStackSequenceDialogBodyDom(activeStack.stack_id);
    const backdrop = document.getElementById("pgStackSequenceBackdrop");
    if (backdrop) {
      backdrop.style.display = "flex";
      backdrop.setAttribute("aria-hidden", "false");
    }
  }

  function closePgStackSequenceDialog() {
    state.stackSequenceDialogOpen = false;
    const backdrop = document.getElementById("pgStackSequenceBackdrop");
    if (backdrop) {
      backdrop.style.display = "none";
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function togglePgStackSequenceDialog(stackId) {
    if (isPgStackSequenceDialogOpen()) {
      closePgStackSequenceDialog();
    } else {
      openPgStackSequenceDialog(stackId);
    }
  }

  function onPgStackSequenceBackdropClick(e) {
    if (e.target && e.target.id === "pgStackSequenceBackdrop") {
      closePgStackSequenceDialog();
    }
  }

  function copyPgStackSequenceReport(stackId) {
    const activeStack = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!activeStack || !Array.isArray(activeStack.commits) || activeStack.commits.length <= 1) {
      showToast("No active stack available to report", 2500);
      return Promise.resolve();
    }
    const commits = activeStack.commits;
    const seq = getPgStackSubmitSequence(activeStack, commits);

    const lines = [];
    lines.push(`### Stack Submit Sequence & Checkpoints — ${activeStack.title || activeStack.stack_id || "Relation Stack"}`);
    lines.push(`**Commits:** ${seq.total_commits} in topological sequence`);
    lines.push(`**Review Progress:** ${seq.reviewed_commits} of ${seq.total_commits} commits reviewed (${Math.round((seq.reviewed_commits / seq.total_commits) * 100)}%)`);
    lines.push(`**Submission Readiness:** ${seq.all_ready ? "✓ Entire stack submittable in sequence!" : `⚠️ Blocked at Step ${seq.bottleneck_step} (ag/${seq.bottleneck_commit ? seq.bottleneck_commit.number : ""}: ${seq.bottleneck_reason})`}`);
    lines.push("");
    lines.push("| Step | Commit | Subject | Review | CR | V | Status |");
    lines.push("| ---: | :--- | :--- | :---: | :---: | :---: | :--- |");

    seq.items.forEach(it => {
      let stText = "Ready";
      if (it.seq_status === "blocked_current") {
        stText = `Blocked: ${it.blocker_reason}`;
      } else if (it.seq_status === "waiting_ancestor") {
        stText = `Waiting on #${it.blocking_step} (ag/${it.blocking_ancestor ? it.blocking_ancestor.number : ""})`;
      } else if (it.seq_status === "merged") {
        stText = "Merged";
      }
      const revText = it.is_reviewed ? "Reviewed ✓" : "Unreviewed";
      const subjEsc = (it.subject || "").replace(/\|/g, "\\|");
      lines.push(`| #${it.step} | ag/${it.number} | ${subjEsc} | ${revText} | ${it.cr_label} | ${it.v_label} | ${stText} |`);
    });

    const reportText = lines.join("\n");
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(reportText).then(() => {
        showToast("Stack submit sequence report copied to clipboard! 📋", 2500);
      }).catch(() => {
        showToast("Stack submit sequence report generated! 📋", 2500);
      });
    } else {
      showToast("Stack submit sequence report generated! 📋", 2500);
      return Promise.resolve();
    }
  }

  function getPgCumulativeStackFiles(activeStack, commits) {
    const rawCommits = Array.isArray(commits) ? commits : (activeStack && Array.isArray(activeStack.commits) ? activeStack.commits : []);
    let cumSig = "";
    for (let i = 0; i < rawCommits.length; i++) {
      const c = rawCommits[i];
      if (!c) continue;
      const clNum = Number(c.number);
      const b = (state.diffsByCl && state.diffsByCl[clNum]) || (state.diffBundleByCl && state.diffBundleByCl[clNum]);
      const files = (b && Array.isArray(b.files)) ? b.files : (Array.isArray(c.files) ? c.files : []);
      if (i > 0) cumSig += ";";
      cumSig += `${clNum}:${(b && b.patch_set) || c.patch_set || 1}:${c.subject || ""}:${files.length}`;
    }
    if (activeStack && activeStack._cachedCumulativeFilesKey === cumSig && activeStack._cachedCumulativeFiles) {
      return activeStack._cachedCumulativeFiles;
    }
    const fileMap = new Map();
    let totalAdd = 0;
    let totalDel = 0;

    rawCommits.forEach((c, idx) => {
      const clNum = Number(c.number);
      const b = (state.diffsByCl && state.diffsByCl[clNum]) || (state.diffBundleByCl && state.diffBundleByCl[clNum]);
      const files = (b && Array.isArray(b.files)) ? b.files : (Array.isArray(c.files) ? c.files : []);
      files.forEach(f => {
        const filePath = String((f && (f.file || f.path)) || "").trim();
        if (!filePath) return;
        const adds = Number((f && f.additions) || 0);
        const dels = Number((f && f.deletions) || 0);
        const status = String((f && f.status) || "M").toUpperCase();
        totalAdd += adds;
        totalDel += dels;

        if (!fileMap.has(filePath)) {
          const ext = typeof getPgFileExtension === "function" ? getPgFileExtension(filePath) : "";
          fileMap.set(filePath, {
            file: filePath,
            extension: ext,
            status,
            additions: 0,
            deletions: 0,
            occurrences: [],
          });
        }
        const entry = fileMap.get(filePath);
        entry.additions += adds;
        entry.deletions += dels;
        entry.occurrences.push({
          commit_idx: idx,
          step: idx + 1,
          number: clNum,
          subject: c.subject || "",
          patch_set: (b && b.patch_set) || c.patch_set || 1,
          additions: adds,
          deletions: dels,
          status,
        });
      });
    });

    const items = Array.from(fileMap.values()).sort((a, b) => {
      if (a.file === "/COMMIT_MSG") return -1;
      if (b.file === "/COMMIT_MSG") return 1;
      if (a.file === "/MERGE_LIST") return -1;
      if (b.file === "/MERGE_LIST") return 1;
      return a.file.localeCompare(b.file);
    });

    const extCounts = {};
    items.forEach(it => {
      if (it.extension) {
        extCounts[it.extension] = (extCounts[it.extension] || 0) + 1;
      }
    });
    const extensions = Object.entries(extCounts)
      .map(([ext, count]) => ({ ext, count }))
      .sort((a, b) => b.count - a.count);

    const result = {
      items,
      total_files: items.length,
      total_additions: totalAdd,
      total_deletions: totalDel,
      total_commits: rawCommits.length,
      extensions,
    };
    if (activeStack) {
      activeStack._cachedCumulativeFilesKey = cumSig;
      activeStack._cachedCumulativeFiles = result;
    }
    return result;
  }

  function renderPgStackFilesDialogBodyDom(stackId) {
    const activeStack = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!activeStack || !Array.isArray(activeStack.commits) || activeStack.commits.length <= 1) {
      closePgStackFilesDrawer();
      return;
    }
    const commits = activeStack.commits;
    const summary = getPgCumulativeStackFiles(activeStack, commits);

    const titleEl = document.getElementById("pgStackFilesTitle");
    if (titleEl) titleEl.textContent = "Cumulative Stack Files";

    const isStackInterdiff = isPgStackInterdiffActive(commits);
    const subEl = document.getElementById("pgStackFilesSubtitle");
    if (subEl) {
      const modePrefix = isStackInterdiff ? "⚡ Interdiff (PS n-1 → PS n): " : "";
      subEl.textContent = `${activeStack.title || activeStack.stack_id || "Relation Stack"} · ${modePrefix}${summary.total_files} unique files touched across ${summary.total_commits} commits (+${summary.total_additions} / -${summary.total_deletions})`;
    }

    const activeExt = state.stackFilesFilterExt || "";
    const extChipsEl = document.getElementById("pgStackFilesExtChips");
    if (extChipsEl) {
      extChipsEl.innerHTML = AemuGerritReact.renderPgStackFilesExtChipsHtml(
        summary.extensions,
        activeExt
      );
    }

    const q = String(state.stackFilesQuery || "").trim().toLowerCase();
    const filtered = summary.items.filter(it => {
      if (activeExt && it.extension !== activeExt) return false;
      if (q && !it.file.toLowerCase().includes(q)) return false;
      return true;
    });

    const tableContainer = document.getElementById("pgStackFilesTableContainer");
    if (tableContainer) {
      tableContainer.innerHTML = AemuGerritReact.renderPgStackFilesTableHtml(
        filtered,
        Number(state.selectedClNumber),
        state.stackFilesQuery || activeExt
      );
    }

    const footerStats = document.getElementById("pgStackFilesFooterStats");
    if (footerStats) {
      footerStats.textContent = `Showing ${filtered.length} of ${summary.total_files} files · +${summary.total_additions} / -${summary.total_deletions}`;
    }
  }

  function openPgStackFilesDrawer(stackId) {
    const activeStack = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!activeStack || !Array.isArray(activeStack.commits) || activeStack.commits.length <= 1) {
      showToast("This change is not part of a multi-commit stack", 2500);
      return;
    }
    state.stackFilesDialogOpen = true;
    renderPgStackFilesDialogBodyDom(activeStack.stack_id);
    const backdrop = document.getElementById("pgStackFilesBackdrop");
    if (backdrop) {
      backdrop.style.display = "flex";
      backdrop.setAttribute("aria-hidden", "false");
    }
    const searchInp = document.getElementById("pgStackFilesSearchInput");
    if (searchInp) {
      searchInp.focus();
    }
  }

  function closePgStackFilesDrawer() {
    state.stackFilesDialogOpen = false;
    const backdrop = document.getElementById("pgStackFilesBackdrop");
    if (backdrop) {
      backdrop.style.display = "none";
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function togglePgStackFilesDrawer(stackId) {
    if (isPgStackFilesDrawerOpen()) {
      closePgStackFilesDrawer();
    } else {
      openPgStackFilesDrawer(stackId);
    }
  }

  function isPgStackFilesDrawerOpen() {
    return Boolean(state.stackFilesDialogOpen);
  }

  function onPgStackFilesBackdropClick(e) {
    if (e.target && e.target.id === "pgStackFilesBackdrop") {
      closePgStackFilesDrawer();
    }
  }

  function onPgStackFilesSearchInput(val) {
    state.stackFilesQuery = String(val || "");
    const clearBtn = document.getElementById("btnClearStackFilesSearch");
    if (clearBtn) {
      clearBtn.style.display = state.stackFilesQuery ? "inline-flex" : "none";
    }
    renderPgStackFilesDialogBodyDom();
  }

  function clearPgStackFilesSearch() {
    state.stackFilesQuery = "";
    const inp = document.getElementById("pgStackFilesSearchInput");
    if (inp) inp.value = "";
    const clearBtn = document.getElementById("btnClearStackFilesSearch");
    if (clearBtn) clearBtn.style.display = "none";
    renderPgStackFilesDialogBodyDom();
  }

  function setPgStackFilesFilterExt(ext) {
    if (state.stackFilesFilterExt === ext) {
      state.stackFilesFilterExt = "";
    } else {
      state.stackFilesFilterExt = ext;
    }
    renderPgStackFilesDialogBodyDom();
  }

  function jumpToStackFileDiff(clNum, filePath) {
    const num = Number(clNum || state.selectedClNumber);
    closePgStackFilesDrawer();
    if (!num || !filePath) return;

    // 1. Uncollapse commit if collapsed
    if (state.collapsedStackCommits && state.collapsedStackCommits.has(num)) {
      state.collapsedStackCommits.delete(num);
      const body = document.getElementById(`pgStackCommitBody-${num}`);
      if (body) body.style.display = "";
      const sum = document.getElementById(`pgStackCommitSummary-${num}`);
      if (sum) sum.style.display = "none";
      const icon = document.querySelector(`#btnToggleStackCommitCollapse-${num} .pg-stack-collapse-icon`);
      if (icon) icon.textContent = "▼";
      const bar = document.querySelector(`.pg-stack-commit-seq-bar[data-cl="${num}"]`);
      if (bar) bar.classList.remove("is-collapsed");
    }

    // 2. Uncollapse file diff block if collapsed
    const escapedPath = filePath.replace(/["\\]/g, "\\$&");
    const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${escapedPath}"]`) ||
                      document.querySelector(`.pg-file-diff-block[data-file="${escapedPath}"]`);
    if (fileBlock && fileBlock.classList.contains("is-collapsed")) {
      const curPs = Number(fileBlock.getAttribute("data-ps") || 1);
      if (typeof setPgFileCollapsed === "function") setPgFileCollapsed(num, curPs, filePath, false);
      fileBlock.classList.remove("is-collapsed");
      const codeTbl = fileBlock.querySelector(".code-table");
      if (codeTbl && codeTbl.style) codeTbl.style.display = "";
    }

    // 3. Scroll to file diff block
    if (fileBlock) {
      if (typeof fileBlock.scrollIntoView === "function") {
        fileBlock.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      fileBlock.classList.remove("pg-header-focused-flash");
      void fileBlock.offsetWidth;
      fileBlock.classList.add("pg-header-focused-flash");
      setTimeout(() => fileBlock.classList.remove("pg-header-focused-flash"), 1800);
    } else {
      scrollToStackCommit(num);
    }

    const baseName = filePath.split("/").pop() || filePath;
    showToast(`Jumped to ${baseName} in ag/${num} ✓`);
  }

  function copyPgStackFilesList(stackId) {
    const activeStack = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!activeStack || !Array.isArray(activeStack.commits) || activeStack.commits.length <= 1) {
      showToast("No active stack available to report", 2500);
      return Promise.resolve();
    }
    const commits = activeStack.commits;
    const summary = getPgCumulativeStackFiles(activeStack, commits);

    const lines = [];
    lines.push(`### Cumulative Stack Files — ${activeStack.title || activeStack.stack_id || "Relation Stack"}`);
    lines.push(`**Summary:** ${summary.total_files} files touched across ${summary.total_commits} commits (+${summary.total_additions} / -${summary.total_deletions})`);
    lines.push("");
    lines.push("| Type | File Path | Delta | Commits |");
    lines.push("| :---: | :--- | :---: | :--- |");

    summary.items.forEach(it => {
      const occStr = it.occurrences.map(o => `Step ${o.step} (ag/${o.number})`).join(", ");
      const fileEsc = it.file.replace(/\|/g, "\\|");
      lines.push(`| ${it.status || "M"} | \`${fileEsc}\` | +${it.additions}/-${it.deletions} | ${occStr} |`);
    });

    const reportText = lines.join("\n");
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(reportText).then(() => {
        showToast("Cumulative stack files list copied to clipboard! 📋", 2500);
      }).catch(() => {
        showToast("Cumulative stack files list generated! 📋", 2500);
      });
    } else {
      showToast("Cumulative stack files list generated! 📋", 2500);
      return Promise.resolve();
    }
  }

  function syncPgRelationSlots() {
    const focusedPnum = (typeof document !== "undefined" && document.activeElement && document.activeElement.classList && document.activeElement.classList.contains("pg-relation-item"))
      ? document.activeElement.getAttribute("data-pnum")
      : null;
    const commits = getActiveCommitsForDiff();
    commits.forEach(c => {
      const relSlot = document.getElementById(`pgRelationSlot-${c.number}`);
      if (relSlot) {
        const relHtml = renderPgRelationChainHtml(c);
        relSlot.innerHTML = relHtml;
        relSlot.style.display = relHtml ? "" : "none";
      }
    });
    if (focusedPnum && typeof document !== "undefined" && typeof document.querySelector === "function") {
      const newFocusedItem = document.querySelector(`.pg-relation-item[data-pnum="${focusedPnum}"]`);
      if (newFocusedItem && typeof newFocusedItem.focus === "function") {
        newFocusedItem.focus();
      }
    }
  }

  function renderPgReplyAttentionChipsHtml(c) {
    if (!c) return "";
    const viewerEmail = String(state.viewer || "").trim().toLowerCase();
    const ownerEmail = String(c.owner_email || "").trim().toLowerCase();
    const isViewerOwner = Boolean(viewerEmail && viewerEmail === ownerEmail);
    const revs = getFallbackReviewersForCl(c).filter(p => !p.is_bot);
    const chips = [];
    const seen = new Set();

    // 1. Owner
    const ownerDisp = c.owner_name || c.owner_short || ownerEmail.split("@")[0] || "Owner";
    const ownerInitial = computePgInitial({ name: ownerDisp, email: ownerEmail });
    const ownerAvatarBg = computePgAvatarColor(ownerEmail || ownerDisp);
    const ownerChecked = !isViewerOwner;
    seen.add(ownerEmail);
    chips.push({
      email: ownerEmail,
      user: c.owner_short || ownerEmail,
      disp: ownerDisp,
      role: "Owner",
      initial: ownerInitial,
      avatarBg: ownerAvatarBg,
      checked: ownerChecked,
    });

    // 2. Reviewers
    revs.forEach(r => {
      const emLower = String(r.email || "").toLowerCase();
      if (!emLower || seen.has(emLower)) return;
      seen.add(emLower);
      const isViewer = Boolean(viewerEmail && (emLower === viewerEmail || String(r.short || "").toLowerCase() === viewerEmail.split("@")[0]));
      const checked = isViewer ? false : (isViewerOwner || Boolean(r.in_attention_set));
      chips.push({
        email: emLower,
        user: r.short || emLower,
        disp: r.name || r.short || emLower,
        role: isViewer ? "You" : "Reviewer",
        initial: computePgInitial(r),
        avatarBg: computePgAvatarColor(emLower),
        checked: checked,
      });
    });

    return chips.map(chip => {
      const activeCls = chip.checked ? " active" : "";
      return `<label class="pg-reply-attention-chip${activeCls}">
        <input type="checkbox" class="pg-reply-attn-cb" data-cl="${c.number}" data-email="${escapeHtml(chip.email)}" data-user="${escapeHtml(chip.user)}" ${chip.checked ? "checked" : ""} onchange="onPgReplyAttentionCheckboxChange(this)" />
        <span class="pg-account-avatar" style="background:${chip.avatarBg};">${escapeHtml(chip.initial)}</span>
        <span class="pg-reply-attn-label">${escapeHtml(chip.disp)}</span>
        <span class="pg-reply-attn-role">(${escapeHtml(chip.role)})</span>
      </label>`;
    }).join("");
  }

  function onPgReplyAttentionCheckboxChange(cb) {
    if (!cb) return;
    const parent =
      typeof cb.closest === "function" ? cb.closest(".pg-reply-attention-chip") : cb;
    if (parent && parent.classList) {
      parent.classList.toggle("active", Boolean(cb.checked));
    }
  }

  const RESTRICTED_NON_VOTING_LABELS = new Set([
    "Dr.No", "Dr-No", "DrNo", "dr.no", "dr-no", "drno", "dr_no",
    "DrNo-Review", "DrNo1-5-Review", "drno-review", "drno1-5-review",
    "drno-q-review", "drno-r-review", "drno-s-review", "drno-t-review",
    "drno-u-review", "drno-v-review", "drno-w-review",
    "NDK-API-Review", "ndk-api-review", "AIDL-API-Review", "aidl-api-review",
    "Build-Team-Review", "build-team-review", "Security-Review", "security-review",
    "Privacy-Review", "privacy-review", "Third-Party-Review", "third-party-review",
    "Android-Architectural-Review", "android-architectural-review",
    "Architecture-Review", "architecture-review",
    "Android-SDK-Compliance-Review", "android-sdk-compliance-review",
    "Global-Approval", "global-approval", "Release-Review", "release-review",
    "Release-Blocker", "release-blocker", "Cherry-Pick-Review", "cherry-pick-review"
  ]);

  function isRestrictedNonVotingLabel(name) {
    if (!name) return true;
    const lower = String(name).trim().toLowerCase();
    const norm = lower.replace(/[-._\s]/g, "");
    // All Dr.No variants (Dr.No, Dr-No, DrNo, DrNo-Review, DrNo1-5-Review, dr.no, etc.)
    if (RESTRICTED_NON_VOTING_LABELS.has(name) || RESTRICTED_NON_VOTING_LABELS.has(lower) || RESTRICTED_NON_VOTING_LABELS.has(norm)) return true;
    if (/^dr[-._]?no/i.test(lower)) return true;
    // Automated bot verification and policy labels
    if (/^slsa[-_]policy/i.test(lower)) return true;
    if (/^code[-_]review[-_]auto[-_]approval/i.test(lower)) return true;
    if (/^presubmit[-_]verified[-_]together/i.test(lower)) return true;
    if (/^coverage[-_]over/i.test(lower)) return true;
    if (/^batchsubmit/i.test(lower)) return true;
    if (/^bypass[-_]batchsubmit/i.test(lower)) return true;
    if (/^build[-_]cop[-_]override/i.test(lower)) return true;
    if (/^exempt$/i.test(lower)) return true;
    // Specialized project review gates that are not voted on standard CLs
    if (/^(build[-_]team|ndk[-_]api|aidl[-_]api|third[-_]party|security|privacy|android[-_]architectural|architecture|android[-_]sdk[-_]compliance)[-_]review/i.test(lower)) return true;
    if (/^(release[-_]review|release[-_]blocker|cherry[-_]pick[-_]review|treehugger[-_]verified|automerger[-_]verified|continuous[-_]integration)/i.test(lower)) return true;
    if (/^global[-_]approval/i.test(lower)) return true;
    return false;
  }

  const _pgLabelScoresTableCache = new Map();
  const PG_LABEL_SCORES_TABLE_CACHE_MAX = 256;
  const _KNOWN_STANDARD_LABELS_LIST = [
    "Code-Review", "Verified", "Presubmit-Verified", "Presubmit-Ready",
    "Autosubmit", "Performance", "Lint", "Bypass-Presubmit", "API-Review",
    "Owners-Override", "Rubber-Stamp", "Commit-Queue", "Open-Source-Licensing", "Trust"
  ];
  const _KNOWN_STANDARD_LABELS_SET = new Set(_KNOWN_STANDARD_LABELS_LIST);

  function renderPgLabelScoresTableHtml(opts = {}) {
    let permMap = {};
    let labelsMap = {};
    let hasPermitted = false;
    let singleClCacheKey = "";
    let singleClObj = null;
    if (opts.isBatch) {
      const checkedNums = Array.isArray(opts.checkedNums)
        ? opts.checkedNums
        : (state.checkedClNumbers ? Array.from(state.checkedClNumbers) : []);
      const checkedSet = new Set(checkedNums.map(Number));
      const checkedCls = (state.changes || []).filter(c => checkedSet.has(Number(c.number)));
      checkedCls.forEach(cl => {
        const rawPerm = (cl && typeof cl.permitted_labels === "object" && cl.permitted_labels) ? cl.permitted_labels : {};
        const rawLabels = (cl && typeof cl.labels === "object" && cl.labels) ? cl.labels : {};
        if (Object.keys(rawPerm).length > 0) hasPermitted = true;
        Object.entries(rawPerm).forEach(([k, vals]) => {
          if (!permMap[k]) permMap[k] = [];
          if (Array.isArray(vals)) {
            vals.forEach(v => {
              if (!permMap[k].includes(v)) permMap[k].push(v);
            });
          }
        });
        Object.keys(rawLabels).forEach(k => {
          labelsMap[k] = rawLabels[k];
        });
      });
    } else {
      singleClObj = opts.cl || (state.changes || []).find(c => Number(c.number) === Number(state.selectedClNumber));
      permMap = (singleClObj && typeof singleClObj.permitted_labels === "object" && singleClObj.permitted_labels) ? singleClObj.permitted_labels : {};
      labelsMap = (singleClObj && typeof singleClObj.labels === "object" && singleClObj.labels) ? singleClObj.labels : {};
      const permKeys = Object.keys(permMap);
      hasPermitted = permKeys.length > 0;
      const permSig = hasPermitted
        ? permKeys.map(k => `${k}:${Array.isArray(permMap[k]) ? permMap[k].join(",") : ""}`).join(";")
        : "";
      const labelSig = Object.keys(labelsMap).join(",");
      const srs = (singleClObj && Array.isArray(singleClObj.submit_requirements)) ? singleClObj.submit_requirements : [];
      const srSig = srs.length > 0
        ? srs.map(sr => `${sr.name || ""}:${sr.status || ""}`).join(";")
        : "";
      singleClCacheKey = `${permSig}|${labelSig}|${srSig}`;
      const cachedTable = _pgLabelScoresTableCache.get(singleClCacheKey);
      if (cachedTable !== undefined) {
        return cachedTable;
      }
    }

    // Collect submit requirements that are NOT_APPLICABLE for this change (or across checked CLs in batch mode)
    const naRequirements = new Set();
    const applicableRequirements = new Set();
    const targetCls = opts.isBatch
      ? (function() {
          const checkedNums = Array.isArray(opts.checkedNums)
            ? opts.checkedNums
            : (state.checkedClNumbers ? Array.from(state.checkedClNumbers) : []);
          const checkedSet = new Set(checkedNums.map(Number));
          return (state.changes || []).filter(c => checkedSet.has(Number(c.number)));
        })()
      : (singleClObj ? [singleClObj] : []);

    function normalizeLabelName(name) {
      return String(name || "").trim().toLowerCase().replace(/[-._\s]/g, "");
    }

    targetCls.forEach(c => {
      if (Array.isArray(c.submit_requirements)) {
        c.submit_requirements.forEach(sr => {
          const sName = String(sr.name || "").trim().toLowerCase();
          const norm = normalizeLabelName(sName);
          const normNoReview = norm.replace(/review$/, "");
          const sStatus = String(sr.status || "").toUpperCase();
          if (sStatus === "NOT_APPLICABLE") {
            naRequirements.add(sName);
            naRequirements.add(norm);
            naRequirements.add(normNoReview);
          } else if (sStatus === "SATISFIED" || sStatus === "UNSATISFIED" || sStatus === "OVERRIDDEN" || sStatus === "BLOCKED") {
            applicableRequirements.add(sName);
            applicableRequirements.add(norm);
            applicableRequirements.add(normNoReview);
          }
        });
      }
    });

    const hasLabels = Object.keys(labelsMap).length > 0;
    const normalizedLabelsMap = new Set();
    if (hasLabels) {
      Object.keys(labelsMap).forEach(k => {
        normalizedLabelsMap.add(String(k).trim().toLowerCase());
        const normK = normalizeLabelName(k);
        normalizedLabelsMap.add(normK);
        normalizedLabelsMap.add(normK.replace(/review$/, ""));
      });
    }

    function isDefinedInLabelsMap(name) {
      if (!name) return false;
      if (!hasLabels) return false;
      const l = String(name).trim().toLowerCase();
      const n = normalizeLabelName(name);
      const nr = n.replace(/review$/, "");
      return Object.prototype.hasOwnProperty.call(labelsMap, name) ||
             normalizedLabelsMap.has(l) ||
             normalizedLabelsMap.has(n) ||
             normalizedLabelsMap.has(nr);
    }

    function getPermittedValues(name) {
      if (!name || !hasPermitted) return null;
      if (Array.isArray(permMap[name])) return permMap[name];
      const lowerName = String(name).trim().toLowerCase();
      const normName = normalizeLabelName(name);
      for (const [k, vals] of Object.entries(permMap)) {
        if (String(k).trim().toLowerCase() === lowerName || normalizeLabelName(k) === normName) {
          return vals;
        }
      }
      return null;
    }

    function isLabelAllowedToVote(labelName) {
      if (!labelName) return false;
      const lower = String(labelName).trim().toLowerCase();
      const norm = normalizeLabelName(labelName);
      const normNoReview = norm.replace(/review$/, "");

      // If this label corresponds to a submit requirement that is NOT_APPLICABLE, suppress it
      const isNa = naRequirements.has(lower) || naRequirements.has(norm) || naRequirements.has(normNoReview);
      const isApplicable = applicableRequirements.has(lower) || applicableRequirements.has(norm) || applicableRequirements.has(normNoReview);
      if (isNa && !isApplicable) {
        return false;
      }

      // Check if this label is defined on the change (in change.labels or an active submit requirement)
      const isDefined = isDefinedInLabelsMap(labelName) || isApplicable;

      // If change.labels is provided, strictly require that the label is defined on this change.
      // In native PolyGerrit (android-review.googlesource.com), inherited permitted_labels contains
      // permissions across All-Projects, but only labels configured on the specific project/change
      // (change.labels) are eligible for voting.
      if (hasLabels && !isDefined) {
        return false;
      }

      // If it is a restricted/bot/release-gate label:
      if (isRestrictedNonVotingLabel(labelName)) {
        // Restricted labels (Dr.No, release gates, security gates, etc.) must NEVER be shown for voting
        // UNLESS the change explicitly has an active, applicable submit requirement requiring this label AND
        // the user has non-zero permitted score on it.
        // On normal branches/CLs (like goldfish emu-master-dev), Dr.No is either not a submit requirement or NOT_APPLICABLE, so it is strictly suppressed.
        if (!isApplicable || isNa) {
          return false;
        }
        const allowedRestricted = getPermittedValues(labelName);
        if (!hasPermitted || !Array.isArray(allowedRestricted)) return false;
        return allowedRestricted.some(v => Number(v) !== 0);
      }

      // If permitted_labels is available from Gerrit, strictly obey it:
      if (hasPermitted) {
        const allowedVals = getPermittedValues(labelName);
        if (!Array.isArray(allowedVals) || !allowedVals.some(v => Number(v) !== 0)) {
          return false;
        }
        // If change.labels is empty (e.g. minimal mock objects), only permit standard review labels by default,
        // or labels that are an applicable submit requirement.
        if (!hasLabels) {
          const coreReviewLabels = new Set(["code-review", "verified", "presubmit-ready", "autosubmit"]);
          if (!coreReviewLabels.has(lower) && !isApplicable) {
            return false;
          }
        }
        return true;
      }

      // Fallback mode when permitted_labels is not available:
      // If labelsMap is present, only allow labels present in labelsMap or core Gerrit labels.
      // If labelsMap is empty (bare mock changes), allow core review labels.
      if (labelName === "Code-Review" || labelName === "Verified") {
        if (hasLabels && !isDefinedInLabelsMap(labelName)) {
          return false;
        }
        return true;
      }
      if (labelName === "Presubmit-Ready" || labelName === "Autosubmit") {
        if (hasLabels) {
          return isDefinedInLabelsMap(labelName);
        }
        return true;
      }
      return false;
    }

    const candidateCustom = new Set([
      ...(hasLabels ? Object.keys(labelsMap) : Object.keys(permMap))
    ]);
    const extraNames = Array.from(candidateCustom).filter(name => {
      if (_KNOWN_STANDARD_LABELS_SET.has(name)) return false;
      return isLabelAllowedToVote(name);
    });

    const allowedStd = new Set();
    for (let i = 0; i < _KNOWN_STANDARD_LABELS_LIST.length; i++) {
      const stdName = _KNOWN_STANDARD_LABELS_LIST[i];
      if (isLabelAllowedToVote(stdName)) allowedStd.add(stdName);
    }
    const hasAnyRenderableLabel = allowedStd.size > 0 || extraNames.length > 0;

    const tableHtml = `<div class="pg-label-scores-table">
      ${!hasAnyRenderableLabel ? `<div class="pg-label-score-empty">No voting permissions available for this change.</div>` : ""}
      ${allowedStd.has("Code-Review") ? `<div class="pg-label-score-row" role="group" aria-label="Code-Review vote">
        <span class="pg-label-score-name">Code-Review</span>
        <div class="pg-label-score-btns">
          <button id="stageCrNeg2" type="button" class="stage-cr-btn cr-neg" data-cr="-2" onclick="toggleStagedCr(-2)" title="Do not submit">-2</button>
          <button id="stageCrNeg1" type="button" class="stage-cr-btn cr-neg" data-cr="-1" onclick="toggleStagedCr(-1)" title="I would prefer that you didn't submit this">-1</button>
          <button id="stageCrZero" type="button" class="stage-cr-btn cr-zero" data-cr="0" onclick="toggleStagedCr(0)" title="No score (Shortcut: 0)"> 0</button>
          <button id="stageCrPlus1" type="button" class="stage-cr-btn cr-pos1" data-cr="1" onclick="toggleStagedCr(1)" title="Looks good to me, but someone else must approve (Shortcut: 1)">+1</button>
          <button id="stageCrPlus2" type="button" class="stage-cr-btn cr-pos2" data-cr="2" onclick="toggleStagedCr(2)" title="Looks good to me, approved (Shortcut: 2 or a)">+2</button>
        </div>
        <span id="pgCrScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Verified") ? `<div class="pg-label-score-row" role="group" aria-label="Verified vote">
        <span class="pg-label-score-name">Verified</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageVNeg1" type="button" class="stage-cr-btn cr-neg" data-v="-1" onclick="toggleStagedVerified(-1)" title="Fails (-1)">-1</button>
          <button id="stageVZero" type="button" class="stage-cr-btn cr-zero" data-v="0" onclick="toggleStagedVerified(0)" title="No score (0)"> 0</button>
          <button id="stageVPlus1" type="button" class="stage-cr-btn cr-pos1" data-v="1" onclick="toggleStagedVerified(1)" title="Verified (+1) — Shortcut: Alt+V">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgVScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Presubmit-Verified") ? `<div class="pg-label-score-row" role="group" aria-label="Presubmit-Verified vote">
        <span class="pg-label-score-name">Presubmit-Verified</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stagePvNeg1" type="button" class="stage-cr-btn cr-neg" data-pv="-1" onclick="toggleStagedPresubmitVerified(-1)" title="Presubmit failed (-1)">-1</button>
          <button id="stagePvZero" type="button" class="stage-cr-btn cr-zero" data-pv="0" onclick="toggleStagedPresubmitVerified(0)" title="No score (0)"> 0</button>
          <button id="stagePvPlus1" type="button" class="stage-cr-btn cr-pos1" data-pv="1" onclick="toggleStagedPresubmitVerified(1)" title="Presubmit verified (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgPvScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Presubmit-Ready") ? `<div class="pg-label-score-row" role="group" aria-label="Presubmit-Ready vote">
        <span class="pg-label-score-name">Presubmit-Ready</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="pgPrZeroBtn" type="button" class="stage-cr-btn cr-zero" onclick="setStagedPrValue(0)" title="Do not trigger presubmit"> 0</button>
          <button id="pgPrPlus1Btn" type="button" class="stage-cr-btn cr-pos1" onclick="setStagedPrValue(1)" title="Trigger presubmit run (Shortcut: p)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgPrScoreDesc" class="pg-label-score-desc"></span>
        <label id="stagePrPill" class="staged-check-pill" style="display:none;">
          <input id="stagePrCheckbox" type="checkbox" onclick="event.stopPropagation(); toggleStagedPr(this.checked)" />
          <span id="stagePrLabel">Presubmit-Ready +1</span>
        </label>
      </div>` : ""}
      ${allowedStd.has("Autosubmit") ? `<div class="pg-label-score-row" role="group" aria-label="Autosubmit vote">
        <span class="pg-label-score-name">Autosubmit</span>
        <div class="pg-label-score-btns" style="width:auto;">
          <label id="stageAsPill" class="pg-autosubmit-label staged-check-pill" title="Automatically submit change when all requirements pass (Shortcut: u)">
            <input id="stageAsCheckbox" type="checkbox" onclick="event.stopPropagation(); toggleStagedAs(this.checked)" />
            <span id="stageAsLabel">Enable Autosubmit (+1)</span>
          </label>
        </div>
        <span class="pg-label-score-desc">Auto-merge once submittable</span>
      </div>` : ""}
      ${allowedStd.has("Performance") ? `<div class="pg-label-score-row" role="group" aria-label="Performance vote">
        <span class="pg-label-score-name">Performance</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stagePerfNeg1" type="button" class="stage-cr-btn cr-neg" data-perf="-1" onclick="toggleStagedPerformance(-1)" title="Performance regression / needs work">-1</button>
          <button id="stagePerfZero" type="button" class="stage-cr-btn cr-zero" data-perf="0" onclick="toggleStagedPerformance(0)" title="No score (0)"> 0</button>
          <button id="stagePerfPlus1" type="button" class="stage-cr-btn cr-pos1" data-perf="1" onclick="toggleStagedPerformance(1)" title="Performance verified / acceptable (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgPerfScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Lint") ? `<div class="pg-label-score-row" role="group" aria-label="Lint vote">
        <span class="pg-label-score-name">Lint</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageLintNeg1" type="button" class="stage-cr-btn cr-neg" data-lint="-1" onclick="toggleStagedLint(-1)" title="Lint issues found / fix required">-1</button>
          <button id="stageLintZero" type="button" class="stage-cr-btn cr-zero" data-lint="0" onclick="toggleStagedLint(0)" title="No score (0)"> 0</button>
          <button id="stageLintPlus1" type="button" class="stage-cr-btn cr-pos1" data-lint="1" onclick="toggleStagedLint(1)" title="Lint clean / verified (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgLintScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Bypass-Presubmit") ? `<div class="pg-label-score-row" role="group" aria-label="Bypass-Presubmit vote">
        <span class="pg-label-score-name">Bypass-Presubmit</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageBpZero" type="button" class="stage-cr-btn cr-zero" data-bp="0" onclick="toggleStagedBypassPresubmit(0)" title="Do not bypass presubmit"> 0</button>
          <button id="stageBpPlus1" type="button" class="stage-cr-btn cr-pos1" data-bp="1" onclick="toggleStagedBypassPresubmit(1)" title="Bypass presubmit requirements (break-glass)">+1</button>
          <button id="stageBpPlus2" type="button" class="stage-cr-btn cr-pos2" data-bp="2" onclick="toggleStagedBypassPresubmit(2)" title="Bypass presubmit requirements (emergency override)">+2</button>
        </div>
        <span id="pgBpScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("API-Review") ? `<div class="pg-label-score-row" role="group" aria-label="API-Review vote">
        <span class="pg-label-score-name">API-Review</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageApiNeg1" type="button" class="stage-cr-btn cr-neg" data-api="-1" onclick="toggleStagedApiReview(-1)" title="API changes rejected">-1</button>
          <button id="stageApiZero" type="button" class="stage-cr-btn cr-zero" data-api="0" onclick="toggleStagedApiReview(0)" title="No score (0)"> 0</button>
          <button id="stageApiPlus1" type="button" class="stage-cr-btn cr-pos1" data-api="1" onclick="toggleStagedApiReview(1)" title="API approved (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgApiScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Owners-Override") ? `<div class="pg-label-score-row" role="group" aria-label="Owners-Override vote">
        <span class="pg-label-score-name">Owners-Override</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageOoZero" type="button" class="stage-cr-btn cr-zero" data-oo="0" onclick="toggleStagedOwnersOverride(0)" title="No override (0)"> 0</button>
          <button id="stageOoPlus1" type="button" class="stage-cr-btn cr-pos1" data-oo="1" onclick="toggleStagedOwnersOverride(1)" title="Override OWNERS approval (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgOoScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Rubber-Stamp") ? `<div class="pg-label-score-row" role="group" aria-label="Rubber-Stamp vote">
        <span class="pg-label-score-name">Rubber-Stamp</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageRsZero" type="button" class="stage-cr-btn cr-zero" data-rs="0" onclick="toggleStagedRubberStamp(0)" title="No score (0)"> 0</button>
          <button id="stageRsPlus1" type="button" class="stage-cr-btn cr-pos1" data-rs="1" onclick="toggleStagedRubberStamp(1)" title="Rubber stamp approved (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgRsScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Commit-Queue") ? `<div class="pg-label-score-row" role="group" aria-label="Commit-Queue vote">
        <span class="pg-label-score-name">Commit-Queue</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageCqZero" type="button" class="stage-cr-btn cr-zero" data-cq="0" onclick="toggleStagedCommitQueue(0)" title="No score (0)"> 0</button>
          <button id="stageCqPlus1" type="button" class="stage-cr-btn cr-pos1" data-cq="1" onclick="toggleStagedCommitQueue(1)" title="Dry run (+1)">+1</button>
          <button id="stageCqPlus2" type="button" class="stage-cr-btn cr-pos2" data-cq="2" onclick="toggleStagedCommitQueue(2)" title="Submit to commit queue (+2)">+2</button>
        </div>
        <span id="pgCqScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Open-Source-Licensing") ? `<div class="pg-label-score-row" role="group" aria-label="Open-Source-Licensing vote">
        <span class="pg-label-score-name">Open-Source-Licensing</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageOslNeg1" type="button" class="stage-cr-btn cr-neg" data-osl="-1" onclick="toggleStagedOpenSourceLicensing(-1)" title="Licensing issue (-1)">-1</button>
          <button id="stageOslZero" type="button" class="stage-cr-btn cr-zero" data-osl="0" onclick="toggleStagedOpenSourceLicensing(0)" title="No score (0)"> 0</button>
          <button id="stageOslPlus1" type="button" class="stage-cr-btn cr-pos1" data-osl="1" onclick="toggleStagedOpenSourceLicensing(1)" title="License compliant (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgOslScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${allowedStd.has("Trust") ? `<div class="pg-label-score-row" role="group" aria-label="Trust vote">
        <span class="pg-label-score-name">Trust</span>
        <div class="pg-label-score-btns">
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
          <button id="stageTrustZero" type="button" class="stage-cr-btn cr-zero" data-trust="0" onclick="toggleStagedTrust(0)" title="No trust score (0)"> 0</button>
          <button id="stageTrustPlus1" type="button" class="stage-cr-btn cr-pos1" data-trust="1" onclick="toggleStagedTrust(1)" title="Trusted (+1)">+1</button>
          <span class="pg-score-placeholder" aria-hidden="true"></span>
        </div>
        <span id="pgTrustScoreDesc" class="pg-label-score-desc"></span>
      </div>` : ""}
      ${(function() {
        if (extraNames.length === 0) return "";
        return extraNames.map(name => {
          const safeName = escapeHtml(name);
          const rawVals = permMap[name];
          const permVals = (Array.isArray(rawVals) && rawVals.length > 0)
            ? rawVals.map(Number).sort((a,b)=>a-b)
            : [-1, 0, 1];
          const buttonsHtml = permVals.map(v => {
            const cls = v > 0 ? (v >= 2 ? "cr-pos2" : "cr-pos1") : (v < 0 ? "cr-neg" : "cr-zero");
            const fmt = v > 0 ? `+${v}` : String(v);
            return `<button type="button" class="stage-cr-btn ${cls}" data-custom-label="${safeName}" data-custom-val="${v}" onclick="toggleStagedCustomLabel('${safeName}', ${v})" title="Stage ${safeName} ${fmt}">${fmt}</button>`;
          }).join("");
          return `<div class="pg-label-score-row" role="group" aria-label="${safeName} vote">
            <span class="pg-label-score-name">${safeName}</span>
            <div class="pg-label-score-btns">${buttonsHtml}</div>
            <span class="pg-label-score-desc" id="pgDesc-${safeName.replace(/[^a-zA-Z0-9_-]/g, '_')}"></span>
          </div>`;
        }).join("");
      })()}
    </div>`;
    if (singleClCacheKey) {
      if (_pgLabelScoresTableCache.size >= PG_LABEL_SCORES_TABLE_CACHE_MAX) {
        _pgLabelScoresTableCache.clear();
      }
      _pgLabelScoresTableCache.set(singleClCacheKey, tableHtml);
    }
    return tableHtml;
  }

  function renderPgReplyDialogHtml(c) {
    const reqCr2 = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
    const policyNote = reqCr2 >= 2
      ? `Agent CL policy: requires ${reqCr2} human Code-Review +2 approvals · notify: OWNER`
      : `Notifications scoped to OWNER (--notify=OWNER)`;
    const savedDraft = loadPgReplyDraft(c.number);
    const initialComment = (state.stagedVote && state.stagedVote.comment) ||
      (savedDraft && savedDraft.comment) || "";
    const hasReplyDraft = Boolean(initialComment && initialComment.trim());
    const canCacheReply = !state.replyDialogOpen && !state.activeReviewerComposer && !state.replyAttentionSelection;
    const revsLen = Array.isArray(c.reviewers_detail) ? c.reviewers_detail.length : 0;
    const ccsLen = Array.isArray(c.ccs_detail) ? c.ccs_detail.length : 0;
    const attLen = Array.isArray(c.attention_set) ? c.attention_set.length : 0;
    const viewerStr = state.viewer || "viewer";
    const extOpen = Boolean(state.extendedVoteControlsOpen);
    if (canCacheReply && c._cachedReplyDialogHtml) {
      const rdSub = c._replyDlgSub;
      if (
        rdSub &&
        rdSub.num === c.number &&
        rdSub.reqCr2 === reqCr2 &&
        rdSub.viewer === viewerStr &&
        rdSub.initialComment === initialComment &&
        rdSub.revsLen === revsLen &&
        rdSub.ccsLen === ccsLen &&
        rdSub.attLen === attLen &&
        rdSub.extOpen === extOpen &&
        rdSub.permRef === c.permitted_labels
      ) {
        return c._cachedReplyDialogHtml;
      }
    }
    const permKeys = c.permitted_labels ? Object.keys(c.permitted_labels).join(",") : "";
    const replyCacheKey = canCacheReply
      ? `${c.number}|${reqCr2}|${viewerStr}|${initialComment}|${revsLen}|${ccsLen}|${attLen}|${extOpen ? 1 : 0}|${permKeys}`
      : "";
    if (replyCacheKey && c._cachedReplyDialogKey === replyCacheKey && c._cachedReplyDialogHtml) {
      c._replyDlgSub = { num: c.number, reqCr2, viewer: viewerStr, initialComment, revsLen, ccsLen, attLen, extOpen, permRef: c.permitted_labels };
      return c._cachedReplyDialogHtml;
    }
    const outHtml = window.AemuGerritReact.renderPgReplyDialogComponentHtml(
      c,
      Boolean(state.replyDialogOpen),
      viewerStr,
      policyNote,
      initialComment,
      hasReplyDraft,
      renderPgReplyParticipantsInnerHtml(c),
      renderPgReplyQuickChipsHtml(c),
      renderPgReplyMarkdownToolbarHtml(c.number),
      renderPgReplyAttentionChipsHtml(c),
      renderPgLabelScoresTableHtml({ isBatch: false, cl: c })
    );
    if (replyCacheKey) {
      c._cachedReplyDialogKey = replyCacheKey;
      c._cachedReplyDialogHtml = outHtml;
      c._replyDlgSub = { num: c.number, reqCr2, viewer: viewerStr, initialComment, revsLen, ccsLen, attLen, extOpen, permRef: c.permitted_labels };
    }
    return outHtml;
  }

  function renderDiffPane(opts = {}) {
    const commits = getActiveCommitsForDiff();
    const container = document.getElementById("diffScroll");
    if (!container) return;
    const clearRenderPassCache = () => {
      state._inRenderDiffPane = false;
      state._renderPassCanNav = undefined;
      _curCommitRenderPass.active = false;
      _curCommitRenderPass.clNum = 0;
      _curCommitRenderPass.refBundle = undefined;
      _curCommitRenderPass.refInfo = null;
      _curCommitRenderPass.mapBundle = undefined;
      _curCommitRenderPass.mapUrl = "";
      for (let i = 0; i < commits.length; i++) {
        const ci = commits[i];
        if (ci) {
          ci._renderPassSubmitTarget = null;
          ci._renderPassDrafts = null;
          ci._renderPassStack = undefined;
        }
      }
    };
    state._inRenderDiffPane = true;
    const nextSig = computeDiffRenderSignature(commits);
    if (
      opts.silentRevalidate &&
      (state.codeSelection ||
        state.replyDialogOpen ||
        state.activeReviewerComposer ||
        state.activeChatComposer ||
        state.batchReviewerComposer ||
        state.stagedVote.noteOpen ||
        Object.keys(state.openReplyByItemId || {}).length > 0 ||
        computeStagedVotePayload(commits).hasAnyChange ||
        nextSig === state.lastRenderedDiffSignature)
    ) {
      clearRenderPassCache();
      syncSwarmIndicatorsOnly();
      return;
    }
    const prevDiffScroll = opts.preserveScroll ? container.scrollTop : null;
    state.lastRenderedDiffSignature = nextSig;
    if (state._lastToolbarVoteBtnsDisabled) state._toolbarVoteDomDirty = true;

    const checkedNums = (state.checkedClNumbers && state.checkedClNumbers.size > 0)
      ? getOrderedCheckedClNumbers()
      : [];
    if (checkedNums.length > 0) {
      container._lastPaneRenderToken = 0;
      container.innerHTML = renderBatchModePanelHtml(checkedNums);
      if (prevDiffScroll !== null) {
        container.scrollTop = prevDiffScroll;
      }
      syncSidebarLayoutDom();
      syncActiveActionButtons(commits, false);
      clearRenderPassCache();
      renderBatchReviewerPopoverDom();
      return;
    }

    if (commits.length === 0) {
      container._lastPaneRenderToken = 0;
      clearRenderPassCache();
      const expandHeaderHtml = `<div style="padding:12px 18px 0;">${renderExpandSidebarButtonHtml()}</div>`;
      if (state.queueClearedCount > 0) {
        container.innerHTML = `${expandHeaderHtml}<div class="inbox-zero-card" style="margin:18px;">
          <div style="font-size:32px;margin-bottom:8px;">🎉</div>
          <div style="font-size:16px;font-weight:700;color:#96d9ab;margin-bottom:6px;">Queue Cleared — All Verified Commits Reviewed!</div>
          <div style="color:var(--g-muted);font-size:12.5px;margin-bottom:16px;">
            You voted <strong>Code-Review +2</strong> on ${state.queueClearedCount} commit${state.queueClearedCount === 1 ? "" : "s"} this session.
          </div>
          <div style="display:flex;justify-content:center;gap:8px;flex-wrap:wrap;">
            <button class="btn btn-approve" onclick="manualRefreshQueue()">↻ Check for New CLs</button>
            <button class="btn" onclick="toggleGate('hideMine')">View Approved CLs</button>
          </div>
        </div>`;
        syncSidebarLayoutDom();
        syncActiveActionButtons(commits, false);
        return;
      }
      container.innerHTML = `${expandHeaderHtml}<div style="padding:24px;color:var(--g-muted);">Select a CL on the left to inspect its diff.</div>`;
      syncSidebarLayoutDom();
      syncActiveActionButtons(commits, false);
      return;
    }

    const activeStack = findPgStackForClOrId(state.selectedClNumber, state.selectedStackId);
    const isContinuous = Boolean(state.continuousStackDiff && activeStack && Array.isArray(activeStack.commits) && activeStack.commits.length > 1);
    const stackBannerHtml = isContinuous ? renderPgContinuousStackBannerHtml(activeStack, commits) : "";
    const sharedPrefs = getPgDiffPreferences();
    const curViewMode = getPgDiffViewMode();
    const curHideLeft = Boolean(state.hideLeftDiffPane);
    const curColorScheme = state.diffColorScheme || "";
    state._renderPassCanNav = canNavigateVisibleCls();
    let toolbarPrefsBaseSig = sharedPrefs._cachedToolbarPrefsBaseSig;
    if (
      !toolbarPrefsBaseSig ||
      sharedPrefs._lastViewMode !== curViewMode ||
      sharedPrefs._lastHideLeft !== curHideLeft ||
      sharedPrefs._lastColorScheme !== curColorScheme
    ) {
      toolbarPrefsBaseSig = `${curViewMode}|${curHideLeft ? 1 : 0}|${curColorScheme}|${sharedPrefs.showDiffOverviewBar !== false ? 1 : 0}|${sharedPrefs.lineWrapping ? 1 : 0}|${sharedPrefs.hideLineNumbers ? 1 : 0}|${sharedPrefs.intralineDifference !== false ? 1 : 0}|${sharedPrefs.context}|${sharedPrefs.tabSize}|${sharedPrefs.lineHeight}|${sharedPrefs.fontSize}|${sharedPrefs.lineLength}|${sharedPrefs.ignoreWhitespace || "IGNORE_NONE"}`;
      sharedPrefs._lastViewMode = curViewMode;
      sharedPrefs._lastHideLeft = curHideLeft;
      sharedPrefs._lastColorScheme = curColorScheme;
      sharedPrefs._cachedToolbarPrefsBaseSig = toolbarPrefsBaseSig;
    }

    let paneHtml = stackBannerHtml;
    let singleSectionRenderSeq = 0;
    for (let cIdx = 0; cIdx < commits.length; cIdx++) {
      const c = commits[cIdx];
      _curCommitRenderPass.active = true;
      _curCommitRenderPass.clNum = Number(c.number);
      _curCommitRenderPass.refBundle = undefined;
      _curCommitRenderPass.refInfo = null;
      _curCommitRenderPass.mapBundle = undefined;
      _curCommitRenderPass.mapUrl = "";
      c._renderPassStack = (commits.length === 1 && Number(c.number) === Number(state.selectedClNumber))
        ? activeStack
        : findPgStackForClOrId(c.number, state.selectedStackId);
      c._renderPassDrafts = (typeof getPendingDraftsForCl === "function") ? getPendingDraftsForCl(c.number) : _PG_EMPTY_ARRAY;
      const bundle = state.diffsByCl[c.number];
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[c.number]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(c.number) : null) || {};
      const cCurrentPs = Number(selPsMeta.ps || (bundle && bundle.patch_set) || c.patch_set || 1);
      if (bundle && selPsMeta && selPsMeta.ps && selPsMeta.base) {
        const targetBase = String(selPsMeta.base);
        const currentBase = String(bundle.base_patch_set || "base");
        const targetPs = Number(selPsMeta.ps);
        const currentPs = Number(bundle.patch_set || 1);
        if (targetBase !== currentBase || targetPs !== currentPs) {
          const cacheKey = `${c.number}:${targetPs}:${targetBase}`;
          if (state.diffsByClPs && state.diffsByClPs[cacheKey]) {
            state.diffsByCl[c.number] = state.diffsByClPs[cacheKey];
          } else if (!state.inFlightDiffPromises[c.number] && typeof selectPgPatchsetComparison === "function") {
            selectPgPatchsetComparison(c.number, targetPs, targetBase, { skipPersistence: true });
          }
        }
      }
      const fileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
      if (fileList.length > 0 && (!state.activeFileByCl || !state.activeFileByCl[c.number])) {
        state.activeFileByCl = state.activeFileByCl || {};
        state.activeFileByCl[c.number] = fileList[0].file;
      }
      const revSet = getPgReviewedSet(c.number, cCurrentPs);
      const collapsedSet = getPgCollapsedSet(c.number, cCurrentPs);
      let query = "";
      let isFilterMissingActive = false;
      let activeStatus = "";
      let commentIdx = null;
      let totalAdd = 0;
      let totalDel = 0;
      let reviewedCount = 0;
      let allCollapsed = fileList.length > 0;
      let allExpanded = fileList.length > 0;
      let hasAnyImageFiles = false;
      let sortedDiffFiles = null;
      let filesHtml = "";
      if (!bundle || !Array.isArray(bundle.files)) {
        filesHtml = renderDiffLoadingPlaceholderHtml(
          `Loading unified diff for ag/${c.number}…`,
          `Fetching patchset PS${cCurrentPs} file hunks and inline comment threads`
        );
      } else if (bundle.files.length === 0) {
        bundle._hasAnyImageFiles = false;
        filesHtml = `<div style="padding:12px;color:var(--g-muted);">No file differences between selected patchsets (commit message or identical patchsets).</div>`;
      } else {
        query = getPgFileQueryFilter(c.number);
        isFilterMissingActive = isPgOwnerFilterActive(c.number);
        activeStatus = (typeof getPgFileStatusFilter === "function") ? getPgFileStatusFilter(c.number) : "";
        sortedDiffFiles = (typeof getPgSortedFileList === "function")
          ? getPgSortedFileList(c, bundle.files)
          : bundle.files;
        commentIdx = (typeof getInlineCommentsIndexForCl === "function")
          ? getInlineCommentsIndexForCl(c.number, bundle)
          : null;
        const csRepoKey = `${c.code_search_base_url || ""}:${c.project || ""}:${c.branch || ""}`;
        const canSkipFilesLoop = Boolean(
          typeof bundle._cachedAllFilesDiffHtml === "string" &&
          bundle._cachedSortedDiffFilesRef === sortedDiffFiles &&
          bundle._cachedSortedDiffFilesLen === sortedDiffFiles.length &&
          !state.activeCommentEditor &&
          !state.activeChatComposer &&
          !bundle._hasSpecialUnmemoizableFile &&
          (typeof window === "undefined" || (
            (!window.renderFileDiffBlock || window.renderFileDiffBlock === renderFileDiffBlock) &&
            (!window.renderFileDiffUnifiedHtml || window.renderFileDiffUnifiedHtml === renderFileDiffUnifiedHtml) &&
            (!window.renderFileDiffSideBySideHtml || window.renderFileDiffSideBySideHtml === renderFileDiffSideBySideHtml) &&
            (!window.renderPgDiffOverviewBarHtml || window.renderPgDiffOverviewBarHtml === renderPgDiffOverviewBarHtml) &&
            (!window.renderPgTrailingContextControlBarHtml || window.renderPgTrailingContextControlBarHtml === renderPgTrailingContextControlBarHtml)
          ))
        );
        const revSig = revSet.size > 0 ? Array.from(revSet).join(",") : "";
        const colSig = collapsedSet.size > 0 ? Array.from(collapsedSet).join(",") : "";
        const expCtxSig = (state.expandedFileContext && state.expandedFileContext.size > 0) ? Array.from(state.expandedFileContext).join(",") : "";
        const blameActiveSig = (state.activeFileBlame && state.activeFileBlame.size > 0) ? Array.from(state.activeFileBlame).join(",") : "";
        const blameCacheLen = hasAnyOwnProp(state.fileBlameCache) ? Object.keys(state.fileBlameCache).length : 0;
        const contentCacheLen = hasAnyOwnProp(state.fileContentCache) ? Object.keys(state.fileContentCache).length : 0;
        const fileModeSig = hasAnyOwnProp(state.fileDiffViewMode)
          ? Object.entries(state.fileDiffViewMode).map(([k, v]) => `${k}:${v}`).join(",")
          : "";
        const outlineSig = state.activeFileOutlineMenu ? `${state.activeFileOutlineMenu.cl}:${state.activeFileOutlineMenu.file}` : "";
        const conflictsLen = (Array.isArray(c.conflicts) ? c.conflicts.length : 0) + (Array.isArray(bundle.conflicts) ? bundle.conflicts.length : 0);
        const ownerStatusSig = isFilterMissingActive
          ? `${(c.reviewers_detail && c.reviewers_detail.length) || 0}:${(c.human_cr2 && c.human_cr2.length) || c.human_cr2_count || 0}`
          : "";
        const allFilesLoopSig = `${c.number}|${cCurrentPs}|${isContinuous ? 1 : 0}|${cIdx}|${activeStack ? (activeStack.stack_id || "") : ""}|${activeStack && Array.isArray(activeStack.commits) ? activeStack.commits.length : 0}|${toolbarPrefsBaseSig}|${sharedPrefs.syntaxHighlighting !== false ? 1 : 0}|${sharedPrefs.showTabs ? 1 : 0}|${sharedPrefs.showWhitespaceErrors !== false ? 1 : 0}|${query || ""}|${isFilterMissingActive ? 1 : 0}|${ownerStatusSig}|${activeStatus || ""}|${revSig}|${colSig}|${commentIdx ? commentIdx.sig : ""}|${csRepoKey}|${expCtxSig}|${blameActiveSig}|${blameCacheLen}|${contentCacheLen}|${fileModeSig}|${outlineSig}|${conflictsLen}`;
        let filesUnchanged = canSkipFilesLoop && bundle._cachedAllFilesLoopSig === allFilesLoopSig;
        if (filesUnchanged) {
          for (let fIdx = 0; fIdx < sortedDiffFiles.length; fIdx++) {
            const f = sortedDiffFiles[fIdx];
            const rLines = Array.isArray(f.lines) ? f.lines : [];
            if (
              f._verLinesRef !== rLines ||
              f._verLinesLen !== rLines.length ||
              f._cachedLoopAdd !== +(f.additions || 0) ||
              f._cachedLoopDel !== +(f.deletions || 0) ||
              f._cachedLoopStatus !== (f.status || "")
            ) {
              filesUnchanged = false;
              break;
            }
          }
        }
        if (filesUnchanged) {
          totalAdd = bundle._cachedTotalAdd;
          totalDel = bundle._cachedTotalDel;
          reviewedCount = bundle._cachedReviewedCount;
          allCollapsed = bundle._cachedAllCollapsed;
          allExpanded = bundle._cachedAllExpanded;
          hasAnyImageFiles = bundle._hasAnyImageFiles;
          filesHtml = bundle._cachedAllFilesDiffHtml;
        } else {
          const prevParts = bundle._cachedDiffBlockParts;
          const canReusePrev = Array.isArray(prevParts) && prevParts.length === sortedDiffFiles.length && typeof bundle._cachedAllFilesDiffHtml === "string";
          const nextParts = new Array(sortedDiffFiles.length);
          let allPartsSame = canReusePrev;
          let hasSpecialUnmemoizable = false;
          bundle._cachedSortedDiffFilesRef = sortedDiffFiles;
          bundle._cachedSortedDiffFilesLen = sortedDiffFiles.length;
          for (let fIdx = 0; fIdx < sortedDiffFiles.length; fIdx++) {
            const f = sortedDiffFiles[fIdx];
            const fPath = f.file || f;
            const addCnt = +(f.additions || 0);
            const delCnt = +(f.deletions || 0);
            totalAdd += addCnt;
            totalDel += delCnt;
            f._cachedLoopAdd = addCnt;
            f._cachedLoopDel = delCnt;
            f._cachedLoopStatus = f.status || "";
            const isFileRev = revSet.has(fPath);
            const isFileCollapsed = collapsedSet.has(fPath);
            if (isFileRev) reviewedCount++;
            if (isFileCollapsed) {
              allExpanded = false;
            } else {
              allCollapsed = false;
            }
            const fStatus = (f._loopStatusFile === f.file && f._loopStatus) || f._cachedStatus || getPgFileStatus(f);
            f._loopStatus = fStatus;
            f._loopStatusFile = f.file;
            const matchesStatus = !activeStatus || fStatus === activeStatus;
            const matchesQuery = !query || matchesPgFileFilter(f.file, query, {
              status: fStatus,
              oldPath: getPgFileOldPath(f),
            });
            const matchesOwner = !isFilterMissingActive || getPgFileOwnerStatus(c, f).status === "missing";
            const isVisible = matchesQuery && matchesOwner && matchesStatus;
            const prevF = fIdx > 0 ? sortedDiffFiles[fIdx - 1] : null;
            const nextF = fIdx < sortedDiffFiles.length - 1 ? sortedDiffFiles[fIdx + 1] : null;
            const partHtml = renderFileDiffBlock(f, c.number, cCurrentPs, isVisible, {
              index: fIdx + 1,
              total: sortedDiffFiles.length,
              prevFile: prevF ? prevF.file : null,
              nextFile: nextF ? nextF.file : null,
              prevFileObj: prevF,
              nextFileObj: nextF,
              change: c,
              stack: activeStack,
              commitIdx: cIdx,
              isRev: isFileRev,
              isCollapsed: isFileCollapsed,
              prefs: sharedPrefs,
              commentIdx,
              csRepoKey,
            });
            if (!hasAnyImageFiles && (f.is_image || f._cachedIsImg)) {
              hasAnyImageFiles = true;
            }
            if (!hasSpecialUnmemoizable && (f.is_image || f.is_binary || f._cachedIsImg || f._cachedIsBin || f._cachedIsMd || f._cachedIsHtml || f.status === "R" || f.status === "C")) {
              hasSpecialUnmemoizable = true;
            }
            nextParts[fIdx] = partHtml;
            if (allPartsSame && prevParts[fIdx] !== partHtml) {
              allPartsSame = false;
            }
          }
          bundle._hasAnyImageFiles = hasAnyImageFiles;
          bundle._hasSpecialUnmemoizableFile = hasSpecialUnmemoizable;
          bundle._cachedTotalAdd = totalAdd;
          bundle._cachedTotalDel = totalDel;
          bundle._cachedReviewedCount = reviewedCount;
          bundle._cachedAllCollapsed = allCollapsed;
          bundle._cachedAllExpanded = allExpanded;
          bundle._cachedSortedDiffFilesRef = sortedDiffFiles;
          bundle._cachedSortedDiffFilesLen = sortedDiffFiles.length;
          bundle._cachedAllFilesLoopSig = allFilesLoopSig;
          if (allPartsSame) {
            filesHtml = bundle._cachedAllFilesDiffHtml;
          } else {
            let accHtml = "";
            for (let fIdx = 0; fIdx < nextParts.length; fIdx++) {
              accHtml += nextParts[fIdx];
            }
            filesHtml = accHtml;
            bundle._cachedDiffBlockParts = nextParts;
            bundle._cachedAllFilesDiffHtml = filesHtml;
          }
        }
      }

      if (bundle && Array.isArray(bundle.code_questions) && bundle.code_questions.length > 0 && typeof mergeTranscriptCodeQuestions === "function") {
        mergeTranscriptCodeQuestions(bundle.code_questions, { silentDom: true, suppressNotifications: true });
      }
      if (bundle && Array.isArray(bundle.unresolved_threads)) {
        for (let tIdx = 0; tIdx < bundle.unresolved_threads.length; tIdx++) {
          const t = bundle.unresolved_threads[tIdx];
          if (!t._ui_item_id) t._ui_item_id = `srv-thread-${c.number}-${tIdx}`;
        }
      }
      const allThreads = (bundle && Array.isArray(bundle.unresolved_threads))
        ? bundle.unresolved_threads
        : [];
      const activeUnresolvedThreads = [];
      let resolvedTotal = 0;
      for (let tIdx = 0; tIdx < allThreads.length; tIdx++) {
        const t = allThreads[tIdx];
        if (t.unresolved !== false) activeUnresolvedThreads.push(t);
        else resolvedTotal++;
      }
      const threadsHtml = activeUnresolvedThreads.length > 0
        ? activeUnresolvedThreads.map(t => `<div class="swarm-finding" data-thread-ui-id="${escapeHtml(t._ui_item_id || "")}">
            <div class="swarm-finding-head">
              <span>Unresolved Comment · ${escapeHtml(t.author_name)} on ${renderCodeSearchFileLinkHtml(c, t.file_path, `${t.file_path}:${t.line}`, t.line)}</span>
              <button class="nudge-btn" onclick="triggerHarnessForCl(${c.number}, 'address-review', this)">→ Auto-Fix with Agent</button>
            </div>
            <div style="color:var(--g-text);font-size:12px;">${escapeHtml(t.latest_message)}</div>
          </div>`).join("")
        : "";

      const aiAnalysisPanelHtml = renderCommitAiAnalysisPanelHtml(c);
      const cUnreviewedCount = fileList.length - reviewedCount;
      const unresTotal = Math.max(Number(c.unresolved_comment_count || 0), activeUnresolvedThreads.length);
      const isHidingResolved = (state.showResolvedComments === false);
      const hideResolvedCls = isHidingResolved ? "pg-hide-resolved" : "";
      const toggleResolvedLabel = isHidingResolved ? "Show resolved" : "Hide resolved";
      const resolvedCountSuffix = resolvedTotal > 0 ? ` (${resolvedTotal})` : "";

      const robotTotal = (typeof countPgRobotComments === "function") ? countPgRobotComments(c.number, bundle) : 0;
      const isHidingRobot = (state.showRobotComments === false);
      const hideRobotCls = isHidingRobot ? "pg-hide-robot-comments" : "";
      const toggleRobotLabel = isHidingRobot ? "Show robot" : "Hide robot";
      const robotCountSuffix = robotTotal > 0 ? ` (${robotTotal})` : "";

      const totalCommentsCount = unresTotal + resolvedTotal + robotTotal;
      const allCommentsCollapsed = (totalCommentsCount > 0) && (typeof areAllPgCommentsCollapsed === "function") && areAllPgCommentsCollapsed(c.number);
      const activeTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[c.number]) || "files";
      const authorsList = (totalCommentsCount > 1 && typeof getPgCommentAuthors === "function") ? getPgCommentAuthors(c.number) : _PG_EMPTY_ARRAY;
      const curAuthorFilter = (typeof getPgCommentsAuthorFilter === "function") ? getPgCommentsAuthorFilter(c.number) : "all";
      const isFilteringAuthor = Boolean(curAuthorFilter && curAuthorFilter !== "all");
      const filterAuthorCls = isFilteringAuthor ? "pg-filter-author-active" : "";
      const draftsChipHtml = renderPgDraftsSummaryChipHtml(c.number, c._renderPassDrafts);
      const robotChipHtml = renderPgRobotCommentsChipHtml(c.number, bundle);

      const utSub = c._unresTabSub || (c._unresTabSub = {});
      let unresolvedTabHtml = "";
      if (
        typeof utSub.html === "string" &&
        utSub.num === c.number &&
        utSub.activeTab === activeTab &&
        utSub.totalCommentsCount === totalCommentsCount &&
        utSub.unresTotal === unresTotal &&
        utSub.resolvedTotal === resolvedTotal &&
        utSub.robotTotal === robotTotal &&
        utSub.isHidingResolved === isHidingResolved &&
        utSub.isHidingRobot === isHidingRobot &&
        utSub.allCommentsCollapsed === allCommentsCollapsed &&
        utSub.authorsLen === authorsList.length &&
        utSub.isFilteringAuthor === isFilteringAuthor &&
        utSub.curAuthorFilter === curAuthorFilter &&
        utSub.draftsChipHtml === draftsChipHtml &&
        utSub.robotChipHtml === robotChipHtml
      ) {
        unresolvedTabHtml = utSub.html;
      } else {
        unresolvedTabHtml = (totalCommentsCount > 0)
          ? `<div class="pg-unresolved-stepper-wrap" id="pgUnresolvedStepper-${c.number}">
              <button type="button" id="pgTabComments-${c.number}" class="pg-tab ${activeTab === "comments" ? "active" : "pg-tab-link"} pg-unresolved-tab-btn" onclick="selectPgChangeTab(${c.number}, 'comments')" title="View Comments &amp; Threads (Shortcut: Shift+C)">
                <span>💬 Comments</span>
                ${unresTotal > 0 ? `<span class="pg-unresolved-pill" style="margin-left:4px;">${unresTotal} unresolved</span>` : `<span class="pg-tab-count" style="margin-left:4px;">(${totalCommentsCount})</span>`}
              </button>
              ${draftsChipHtml}
              ${robotChipHtml}
              ${unresTotal > 0 ? `
                <div class="pg-unresolved-stepper-arrows">
                  <button type="button" class="pg-btn-thread-step" onclick="navigatePgUnresolvedThread('prev', ${c.number})" title="Previous unresolved comment thread (Shortcut: Shift+P)">▲</button>
                  <button type="button" class="pg-btn-thread-step" onclick="navigatePgUnresolvedThread('next', ${c.number})" title="Next unresolved comment thread (Shortcut: Shift+N)">▼</button>
                </div>
              ` : ""}
              <button id="btnToggleAllCommentsCollapse-${c.number}" type="button" class="pg-btn-toggle-all-comments ${allCommentsCollapsed ? "is-all-collapsed" : "is-all-expanded"}" onclick="togglePgAllCommentsCollapse(${c.number})" title="Expand or collapse all comment threads across change (Shortcut: Shift+E)">
                <span class="pg-btn-icon">${allCommentsCollapsed ? "⤢" : "⤡"}</span> ${allCommentsCollapsed ? "Expand all" : "Collapse all"}
              </button>
              ${authorsList.length > 1 ? `
                <button id="btnToggleAuthorComments-${c.number}" type="button" class="pg-btn-toggle-author ${isFilteringAuthor ? "is-filtering-author" : ""}" onclick="cyclePgCommentsAuthorFilter(${c.number})" title="Cycle comment author filter: ${isFilteringAuthor ? escapeHtml(curAuthorFilter) : "All authors"} (Shortcut: Shift+Alt+C)">
                  <span class="pg-btn-icon">👤</span> <span class="pg-btn-author-label">${isFilteringAuthor ? escapeHtml(curAuthorFilter) : "All authors"}</span>
                </button>
              ` : ""}
              ${robotTotal > 0 ? `
                <button id="btnToggleRobotComments-${c.number}" type="button" class="pg-btn-toggle-robot ${isHidingRobot ? "is-hiding-robot" : ""}" data-robot-count="${robotTotal}" onclick="togglePgShowRobotComments(${c.number})" title="Toggle showing automated robot comments (Shortcut: Shift+W)">
                  ${toggleRobotLabel}${robotCountSuffix}
                </button>
              ` : ""}
              ${resolvedTotal > 0 ? `
                <button id="btnToggleResolvedComments-${c.number}" type="button" class="pg-btn-toggle-resolved ${isHidingResolved ? "is-hiding-resolved" : ""}" data-resolved-count="${resolvedTotal}" onclick="togglePgShowResolvedComments(${c.number})" title="Toggle showing resolved comment threads (Shortcut: Shift+R)">
                  ${toggleResolvedLabel}${resolvedCountSuffix}
                </button>
              ` : ""}
            </div>`
          : `<div class="pg-unresolved-stepper-wrap" id="pgUnresolvedStepper-${c.number}">
              <button type="button" id="pgTabComments-${c.number}" class="pg-tab ${activeTab === "comments" ? "active" : "pg-tab-link"} pg-unresolved-tab-btn" onclick="selectPgChangeTab(${c.number}, 'comments')" title="View Comments &amp; Threads">
                <span>💬 Comments</span> <span class="pg-tab-count">(0)</span>
              </button>
              ${draftsChipHtml}
              ${robotChipHtml}
            </div>`;
        utSub.num = c.number;
        utSub.activeTab = activeTab;
        utSub.totalCommentsCount = totalCommentsCount;
        utSub.unresTotal = unresTotal;
        utSub.resolvedTotal = resolvedTotal;
        utSub.robotTotal = robotTotal;
        utSub.isHidingResolved = isHidingResolved;
        utSub.isHidingRobot = isHidingRobot;
        utSub.allCommentsCollapsed = allCommentsCollapsed;
        utSub.authorsLen = authorsList.length;
        utSub.isFilteringAuthor = isFilteringAuthor;
        utSub.curAuthorFilter = curAuthorFilter;
        utSub.draftsChipHtml = draftsChipHtml;
        utSub.robotChipHtml = robotChipHtml;
        utSub.html = unresolvedTabHtml;
      }
      const revShortHtml = (bundle && bundle.revision_short)
        ? `<span class="pg-sha-pill" title="Current patchset commit">${escapeHtml(bundle.revision_short)}</span>`
        : "";
      const relHtml = renderPgRelationChainHtml(c);
      const totalsHtml = fileList.length > 0
        ? `<div class="pg-files-totals"><span class="pg-files-add">+${totalAdd}</span><span class="pg-files-del">-${totalDel}</span></div>`
        : "";

      const isHideReviewed = (typeof isPgHideReviewedActive === "function") && isPgHideReviewedActive(c.number);
      const isFilterCommentsActive = (typeof isPgCommentsFilterActive === "function") && isPgCommentsFilterActive(c.number);
      const activeExt = (typeof getPgFileExtensionFilter === "function") ? getPgFileExtensionFilter(c.number) : "";
      const activeSize = (typeof getPgFileSizeFilter === "function") ? getPgFileSizeFilter(c.number) : "";
      const hasExtraFileFilter = Boolean(
        query ||
        isFilterMissingActive ||
        activeStatus ||
        isHideReviewed ||
        isFilterCommentsActive ||
        activeExt ||
        activeSize
      );
      const visibleFiles = (hasExtraFileFilter && typeof getPgFilteredAndSortedFiles === "function")
        ? getPgFilteredAndSortedFiles(c, fileList)
        : fileList;
      const isFiltered = fileList.length > 0 && visibleFiles.length !== fileList.length;
      const visibleReviewedCount = isFiltered ? visibleFiles.filter(f => revSet.has(f.file)).length : reviewedCount;
      const isAllRev = visibleFiles.length > 0 && visibleReviewedCount === visibleFiles.length;
      if (bundle) {
        const flCtx = bundle._flCtxObj || (bundle._flCtxObj = {});
        flCtx.curPs = cCurrentPs;
        flCtx.revSet = revSet;
        flCtx.collapsedSet = collapsedSet;
        flCtx.commentIdx = commentIdx;
        flCtx.query = query;
        flCtx.isFilterMissingActive = isFilterMissingActive;
        flCtx.activeStatus = activeStatus;
        flCtx.isHideReviewed = isHideReviewed;
        flCtx.isFilterCommentsActive = isFilterCommentsActive;
        flCtx.activeExt = activeExt;
        flCtx.activeSize = activeSize;
        flCtx.sortedFiles = sortedDiffFiles;
        bundle._renderPassFlCtx = flCtx;
      }
      const fileListTableHtml = renderPgFileListHtml(c, bundle);
      if (bundle) bundle._renderPassFlCtx = null;
      const rawMsgs = (c && Array.isArray(c.messages))
        ? c.messages
        : ((bundle && Array.isArray(bundle.messages)) ? bundle.messages : []);
      const msgCount = rawMsgs.length;
      const stackSeqBarHtml = (isContinuous && commits.length > 1)
        ? renderPgStackCommitSequenceBarHtml(c, cIdx, commits.length, activeStack)
        : "";
      const isCommitCollapsed = isContinuous && state.collapsedStackCommits.has(Number(c.number));

      const changeHeaderHtml = renderPgChangeHeaderHtml(c, bundle);
      const rebaseBannerHtml = renderPgRebaseNoticeBannerHtml(c, bundle);
      const reviewersPanelHtml = renderCommitReviewersPanelHtml(c, false);
      const commitMsgPanelHtml = renderCommitMessagePanelHtml(c, bundle);
      const mergeListPanelHtml = renderMergeListPanelHtml(c, bundle);
      const presubmitRowHtml = renderCommitPresubmitRowHtml(c);
      const replyDialogHtml = renderPgReplyDialogHtml(c);
      const presubmitDrawerHtml = renderPresubmitTargetsDrawerHtml(c);
      const swarmPanelHtml = renderCommitSwarmPanelHtml(c.number);
      const checksTabBtnHtml = renderPgChecksTabButtonHtml(c, activeTab);
      const ownersSummaryHtml = fileList.length > 0 ? renderPgOwnersSummaryHtml(c, bundle) : "";
      const psSelectorHtml = renderPgPatchsetSelectorHtml(c, bundle);
      const jumpMenuHtml = renderPgFileJumpMenuHtml(c, bundle);
      const diffFindBtnHtml = renderPgDiffFindButtonHtml(c.number);
      const csSub = c._commitSectionSubCache || (c._commitSectionSubCache = {});
      const toolbarPrefsSig = `${c.number}|${toolbarPrefsBaseSig}`;
      const reuseToolbarPrefs = c._cachedDiffToolbarPrefsSig === toolbarPrefsSig;
      const goToLineBtnHtml = reuseToolbarPrefs ? csSub.goToLineBtnHtml : renderPgDiffGoToLineButtonHtml(c.number);
      const diffModeSelHtml = reuseToolbarPrefs ? csSub.diffModeSelHtml : renderPgDiffModeSelectorHtml();
      const leftPaneBtnHtml = reuseToolbarPrefs ? csSub.leftPaneBtnHtml : renderPgDiffLeftPaneToggleButtonHtml(c.number);
      const overviewBtnHtml = reuseToolbarPrefs ? csSub.overviewBtnHtml : renderPgDiffOverviewToggleButtonHtml(c.number);
      const wrapBtnHtml = reuseToolbarPrefs ? csSub.wrapBtnHtml : renderPgDiffWrapButtonHtml(c.number);
      const lineNumsBtnHtml = reuseToolbarPrefs ? csSub.lineNumsBtnHtml : renderPgDiffLineNumbersButtonHtml(c.number);
      const intralineBtnHtml = reuseToolbarPrefs ? csSub.intralineBtnHtml : renderPgDiffIntralineButtonHtml(c.number);
      const ctxSelHtml = reuseToolbarPrefs ? csSub.ctxSelHtml : renderPgDiffContextSelectHtml(c.number);
      const tabSizeSelHtml = reuseToolbarPrefs ? csSub.tabSizeSelHtml : renderPgDiffTabSizeSelectHtml(c.number);
      const lineHeightStepHtml = reuseToolbarPrefs ? csSub.lineHeightStepHtml : renderPgDiffLineHeightStepperHtml(c.number);
      const fontSizeStepHtml = reuseToolbarPrefs ? csSub.fontSizeStepHtml : renderPgDiffFontSizeStepperHtml(c.number);
      const lineLenSelHtml = reuseToolbarPrefs ? csSub.lineLenSelHtml : renderPgDiffLineLengthSelectHtml(c.number);
      const wsSelHtml = reuseToolbarPrefs ? csSub.wsSelHtml : renderPgDiffWhitespaceSelectHtml(c.number);
      const prefsBtnHtml = reuseToolbarPrefs ? csSub.prefsBtnHtml : renderPgDiffPreferencesButtonHtml();
      const colorSchemeSelHtml = reuseToolbarPrefs ? csSub.colorSchemeSelHtml : renderDiffColorSchemeSelectHtml();
      const checksCtrlsHtml = renderPgChecksControlsHtml(c);
      const interdiffBannerHtml = renderPgInterdiffBannerHtml(c, bundle);
      const diffFindBarHtml = renderPgDiffFindBarHtml(c.number);
      const commentsViewHtml = renderPgCommentsViewHtml(c, bundle);
      const checksViewHtml = renderPgChecksViewHtml(c);
      const changeLogHtml = renderPgChangeLogSectionHtml(c, bundle);

      const commitSectionScalarsSig = `${isContinuous ? 1 : 0}|${isCommitCollapsed ? 1 : 0}|${hideResolvedCls}|${hideRobotCls}|${filterAuthorCls}|${cCurrentPs}|${activeTab}|${fileList.length}|${visibleFiles.length}|${reviewedCount}|${visibleReviewedCount}|${allCollapsed ? 1 : 0}|${allExpanded ? 1 : 0}|${msgCount}`;
      if (
        csSub.scalarsSig === commitSectionScalarsSig &&
        csSub.stackSeqBarHtml === stackSeqBarHtml &&
        csSub.changeHeaderHtml === changeHeaderHtml &&
        csSub.rebaseBannerHtml === rebaseBannerHtml &&
        csSub.reviewersPanelHtml === reviewersPanelHtml &&
        csSub.revShortHtml === revShortHtml &&
        csSub.commitMsgPanelHtml === commitMsgPanelHtml &&
        csSub.mergeListPanelHtml === mergeListPanelHtml &&
        csSub.presubmitRowHtml === presubmitRowHtml &&
        csSub.relHtml === relHtml &&
        csSub.replyDialogHtml === replyDialogHtml &&
        csSub.presubmitDrawerHtml === presubmitDrawerHtml &&
        csSub.swarmPanelHtml === swarmPanelHtml &&
        csSub.aiAnalysisPanelHtml === aiAnalysisPanelHtml &&
        csSub.threadsHtml === threadsHtml &&
        csSub.checksTabBtnHtml === checksTabBtnHtml &&
        csSub.unresolvedTabHtml === unresolvedTabHtml &&
        csSub.ownersSummaryHtml === ownersSummaryHtml &&
        csSub.psSelectorHtml === psSelectorHtml &&
        csSub.jumpMenuHtml === jumpMenuHtml &&
        csSub.totalsHtml === totalsHtml &&
        csSub.diffFindBtnHtml === diffFindBtnHtml &&
        csSub.goToLineBtnHtml === goToLineBtnHtml &&
        csSub.diffModeSelHtml === diffModeSelHtml &&
        csSub.leftPaneBtnHtml === leftPaneBtnHtml &&
        csSub.overviewBtnHtml === overviewBtnHtml &&
        csSub.wrapBtnHtml === wrapBtnHtml &&
        csSub.lineNumsBtnHtml === lineNumsBtnHtml &&
        csSub.intralineBtnHtml === intralineBtnHtml &&
        csSub.ctxSelHtml === ctxSelHtml &&
        csSub.tabSizeSelHtml === tabSizeSelHtml &&
        csSub.lineHeightStepHtml === lineHeightStepHtml &&
        csSub.fontSizeStepHtml === fontSizeStepHtml &&
        csSub.lineLenSelHtml === lineLenSelHtml &&
        csSub.wsSelHtml === wsSelHtml &&
        csSub.prefsBtnHtml === prefsBtnHtml &&
        csSub.colorSchemeSelHtml === colorSchemeSelHtml &&
        csSub.checksCtrlsHtml === checksCtrlsHtml &&
        csSub.interdiffBannerHtml === interdiffBannerHtml &&
        csSub.fileListTableHtml === fileListTableHtml &&
        csSub.diffFindBarHtml === diffFindBarHtml &&
        csSub.filesHtml === filesHtml &&
        csSub.commentsViewHtml === commentsViewHtml &&
        csSub.checksViewHtml === checksViewHtml &&
        csSub.changeLogHtml === changeLogHtml &&
        c._cachedCommitSectionHtml
      ) {
        singleSectionRenderSeq = csSub.renderSeq || 0;
        paneHtml = paneHtml ? (paneHtml + c._cachedCommitSectionHtml) : c._cachedCommitSectionHtml;
        continue;
      }

      const commitSectionHtml = `<div id="pgStackCommit-${c.number}" class="pg-change-view commit-section ${isContinuous ? "pg-stack-commit-section" : ""} ${isCommitCollapsed ? "is-collapsed" : ""} ${hideResolvedCls} ${hideRobotCls} ${filterAuthorCls}" data-cl="${c.number}">
        ${stackSeqBarHtml}
        <div id="pgStackCommitBody-${c.number}" class="pg-stack-commit-body" style="${isCommitCollapsed ? "display:none;" : ""}">
          ${changeHeaderHtml}
          ${rebaseBannerHtml}
          <section class="pg-change-info">
            ${reviewersPanelHtml}
            <div class="pg-main-change-info">
              <div class="pg-commit-and-related">
                <div class="pg-commit-container">
                  <div class="pg-reply-bar">
                    <div class="pg-reply-bar-left">
                      <button id="btnOpenReplyDialog" type="button" class="pg-btn-primary pg-reply-btn" onclick="event.stopPropagation(); openReplyDialog({ focusComment: true })" title="Reply and vote on ag/${c.number} (Shortcut: r)">Reply</button>
                      <span id="pgReplyStagedHint" class="pg-staged-indicator"></span>
                    </div>
                    <div class="pg-reply-bar-right pg-patchset-caption">
                      <span id="pgReplyBarPs-${c.number}">Patchset ${cCurrentPs}</span>
                      ${revShortHtml}
                    </div>
                  </div>
                  ${commitMsgPanelHtml}
                  ${mergeListPanelHtml}
                  <div id="presubmitRowSlot-${c.number}">${presubmitRowHtml}</div>
                </div>
                <div id="pgRelationSlot-${c.number}" class="pg-commit-aside" style="${relHtml ? "" : "display:none;"}">${relHtml}</div>
              </div>
            </div>
          </section>
          ${replyDialogHtml}
          <div id="presubmitDrawerSlot-${c.number}" class="pg-drawer-slot">${presubmitDrawerHtml}</div>
          <div class="pg-aux-panels">
            <div id="swarmPanel-${c.number}">${swarmPanelHtml}</div>
            ${aiAnalysisPanelHtml}
            ${threadsHtml}
          </div>
          <div id="pgTabsBar-${c.number}" class="pg-tabs-bar">
            <div class="pg-tabs-left">
              <a href="javascript:void(0)" id="pgTabFiles-${c.number}" class="pg-tab ${activeTab === "files" ? "active" : "pg-tab-link"}" onclick="selectPgChangeTab(${c.number}, 'files')" title="View Files &amp; Diffs (Shortcut: Shift+K)">Files <span class="pg-tab-count">(${isFiltered ? `${visibleFiles.length}/${fileList.length}` : fileList.length})</span></a>
              ${checksTabBtnHtml}
              ${unresolvedTabHtml}
              <a href="javascript:void(0)" class="pg-tab pg-tab-link" onclick="scrollToChangeLog(${c.number})" title="Jump to Change Log / Messages (Shortcut: m)">Change Log <span class="pg-tab-count">(${msgCount})</span></a>
              <div id="pgFilesTabSubControls-${c.number}" class="pg-tab-subcontrols" style="${activeTab === "files" ? "display:inline-flex;align-items:center;gap:10px;" : "display:none;"}">
                ${fileList.length > 0 ? `
                  <button id="btnToggleAllDiffs-${c.number}" type="button" class="pg-btn-subtle pg-expand-all-btn" onclick="toggleAllPgFileDiffs(${c.number}, ${cCurrentPs})" title="Expand or collapse all file diffs (Shortcut: Shift+I)">
                    ${allCollapsed ? "Expand All (Shift+I)" : "Collapse All (Shift+I)"}
                  </button>
                  <button id="btnExpandAllHeader-${c.number}" type="button" class="pg-btn-subtle pg-btn-expand-all-files ${allExpanded ? "is-disabled" : ""}" data-cl="${c.number}" onclick="expandAllPgFileDiffs(${c.number}, ${cCurrentPs})" title="Expand all file diffs in change (Shortcut: Shift+I)" ${allExpanded ? "disabled" : ""}>
                    <span class="pg-btn-icon" aria-hidden="true">☷</span> Expand all <span class="pg-shortcut-hint">Shift+I</span>
                  </button>
                  <button id="btnCollapseAllHeader-${c.number}" type="button" class="pg-btn-subtle pg-btn-collapse-all-files ${allCollapsed ? "is-disabled" : ""}" data-cl="${c.number}" onclick="collapseAllPgFileDiffs(${c.number}, ${cCurrentPs})" title="Collapse all file diffs in change (Shortcut: Shift+E)" ${allCollapsed ? "disabled" : ""}>
                    <span class="pg-btn-icon" aria-hidden="true">☰</span> Collapse all <span class="pg-shortcut-hint">Shift+E</span>
                  </button>
                  <button id="btnExpandUnreviewedHeader-${c.number}" type="button" class="pg-btn-subtle pg-btn-expand-unreviewed" onclick="expandPgUnreviewedFileDiffs(${c.number}, ${cCurrentPs})" title="Expand diffs for only unreviewed files, collapsing reviewed ones">
                    ⚡ Expand unreviewed
                  </button>
                  <button id="btnNextUnreviewedHeader-${c.number}" type="button" class="pg-btn-subtle pg-btn-next-unreviewed" onclick="navigatePgUnreviewedFileDiff('next', null, ${c.number})" title="Jump to next unreviewed file diff (Shortcut: Alt+])" style="${cUnreviewedCount > 0 ? "" : "display:none;"}">
                    ⚡ Next unreviewed (${cUnreviewedCount})
                  </button>
                  <span id="badgeAllReviewedHeader-${c.number}" class="pg-all-reviewed-badge" style="${cUnreviewedCount === 0 && fileList.length > 0 ? "" : "display:none;"}">✓ All reviewed</span>
                  <button id="btnExpandCommentsHeader-${c.number}" type="button" class="pg-btn-subtle pg-btn-expand-comments" onclick="expandPgCommentsFileDiffs(${c.number}, ${cCurrentPs})" title="Expand diffs for files with comments or drafts, collapsing files without comments">
                    💬 Expand commented
                  </button>
                  <span id="pgReviewedSummary-${c.number}" class="pg-reviewed-summary-badge is-clickable ${isAllRev ? "all-reviewed" : ""}" onclick="togglePgMarkAllReviewed(${c.number})" title="Click to toggle mark all files as reviewed (Shift+V)" role="button" tabindex="0">
                    ${isFiltered ? `${visibleReviewedCount} / ${visibleFiles.length} filtered reviewed` : (isAllRev ? `All ${fileList.length} files reviewed ✓` : `${reviewedCount} / ${fileList.length} reviewed`)}
                  </span>
                  ${ownersSummaryHtml}
                ` : ""}
              </div>
            </div>
            <div class="pg-tabs-right">
              <div id="pgFilesControls-${c.number}" style="${activeTab === "files" ? "display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap;" : "display:none;"}">
                ${psSelectorHtml}
                ${jumpMenuHtml}
                ${totalsHtml}
                ${diffFindBtnHtml}
                ${goToLineBtnHtml}
                ${diffModeSelHtml}
                ${leftPaneBtnHtml}
                ${overviewBtnHtml}
                ${wrapBtnHtml}
                ${lineNumsBtnHtml}
                ${intralineBtnHtml}
                ${ctxSelHtml}
                ${tabSizeSelHtml}
                ${lineHeightStepHtml}
                ${fontSizeStepHtml}
                ${lineLenSelHtml}
                ${wsSelHtml}
                ${prefsBtnHtml}
                ${colorSchemeSelHtml}
              </div>
              <div id="pgChecksControls-${c.number}" style="${activeTab === "checks" ? "display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap;" : "display:none;"}">
                ${checksCtrlsHtml}
              </div>
            </div>
          </div>
          <div id="pgFilesTabPane-${c.number}" class="pg-tab-pane ${activeTab === "files" ? "is-active" : ""}" style="${activeTab === "files" ? "display:block;" : "display:none;"}">
            ${interdiffBannerHtml}
            ${fileListTableHtml}
            ${diffFindBarHtml}
            <div class="pg-files-container">${filesHtml}</div>
          </div>
          <div id="pgCommentsTabPane-${c.number}" class="pg-tab-pane ${activeTab === "comments" ? "is-active" : ""}" style="${activeTab === "comments" ? "display:block;" : "display:none;"}">
            ${commentsViewHtml}
          </div>
          <div id="pgChecksTabPane-${c.number}" class="pg-tab-pane ${activeTab === "checks" ? "is-active" : ""}" style="${activeTab === "checks" ? "display:block;" : "display:none;"}">
            ${checksViewHtml}
          </div>
          ${changeLogHtml}
        </div>
      </div>`;
      csSub.scalarsSig = commitSectionScalarsSig;
      csSub.stackSeqBarHtml = stackSeqBarHtml;
      csSub.changeHeaderHtml = changeHeaderHtml;
      csSub.rebaseBannerHtml = rebaseBannerHtml;
      csSub.reviewersPanelHtml = reviewersPanelHtml;
      csSub.revShortHtml = revShortHtml;
      csSub.commitMsgPanelHtml = commitMsgPanelHtml;
      csSub.mergeListPanelHtml = mergeListPanelHtml;
      csSub.presubmitRowHtml = presubmitRowHtml;
      csSub.relHtml = relHtml;
      csSub.replyDialogHtml = replyDialogHtml;
      csSub.presubmitDrawerHtml = presubmitDrawerHtml;
      csSub.swarmPanelHtml = swarmPanelHtml;
      csSub.aiAnalysisPanelHtml = aiAnalysisPanelHtml;
      csSub.threadsHtml = threadsHtml;
      csSub.checksTabBtnHtml = checksTabBtnHtml;
      csSub.unresolvedTabHtml = unresolvedTabHtml;
      csSub.ownersSummaryHtml = ownersSummaryHtml;
      csSub.psSelectorHtml = psSelectorHtml;
      csSub.jumpMenuHtml = jumpMenuHtml;
      csSub.totalsHtml = totalsHtml;
      csSub.diffFindBtnHtml = diffFindBtnHtml;
      c._cachedDiffToolbarPrefsSig = toolbarPrefsSig;
      csSub.goToLineBtnHtml = goToLineBtnHtml;
      csSub.diffModeSelHtml = diffModeSelHtml;
      csSub.leftPaneBtnHtml = leftPaneBtnHtml;
      csSub.overviewBtnHtml = overviewBtnHtml;
      csSub.wrapBtnHtml = wrapBtnHtml;
      csSub.lineNumsBtnHtml = lineNumsBtnHtml;
      csSub.intralineBtnHtml = intralineBtnHtml;
      csSub.ctxSelHtml = ctxSelHtml;
      csSub.tabSizeSelHtml = tabSizeSelHtml;
      csSub.lineHeightStepHtml = lineHeightStepHtml;
      csSub.fontSizeStepHtml = fontSizeStepHtml;
      csSub.lineLenSelHtml = lineLenSelHtml;
      csSub.wsSelHtml = wsSelHtml;
      csSub.prefsBtnHtml = prefsBtnHtml;
      csSub.colorSchemeSelHtml = colorSchemeSelHtml;
      csSub.checksCtrlsHtml = checksCtrlsHtml;
      csSub.interdiffBannerHtml = interdiffBannerHtml;
      csSub.fileListTableHtml = fileListTableHtml;
      csSub.diffFindBarHtml = diffFindBarHtml;
      csSub.filesHtml = filesHtml;
      csSub.commentsViewHtml = commentsViewHtml;
      csSub.checksViewHtml = checksViewHtml;
      csSub.changeLogHtml = changeLogHtml;
      csSub.renderSeq = ++_nextCommitSectionRenderSeq;
      singleSectionRenderSeq = csSub.renderSeq;
      c._cachedCommitSectionHtml = commitSectionHtml;
      paneHtml = paneHtml ? (paneHtml + commitSectionHtml) : commitSectionHtml;
    }
    const nextPaneToken = (commits.length === 1 && !stackBannerHtml) ? singleSectionRenderSeq : 0;
    if (!nextPaneToken || container._lastPaneRenderToken !== nextPaneToken || container.innerHTML.length === 0) {
      container.innerHTML = paneHtml;
      container._lastPaneRenderToken = nextPaneToken;
    }
    container._diffRenderGeneration = (container._diffRenderGeneration || 0) + 1;
    if (prevDiffScroll !== null) {
      container.scrollTop = prevDiffScroll;
    }
    for (let cIdx = 0; cIdx < commits.length; cIdx++) {
      const c = commits[cIdx];
      const bundle = state.diffsByCl && state.diffsByCl[c.number];
      const hasMessages = (Array.isArray(c.messages) && c.messages.length > 0) || (bundle && Array.isArray(bundle.messages) && bundle.messages.length > 0);
      if (hasMessages) {
        applyPgMessageFilters(c.number);
      }
      if (state.diffFind && state.diffFind.isOpen && Number(state.diffFind.cl) === Number(c.number) && state.diffFind.query) {
        executePgDiffSearch(c.number);
      }
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[c.number]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(c.number) : null) || {};
      const curPs = Number(selPsMeta.ps || (bundle && bundle.patch_set) || c.patch_set || 1);
      syncPgMasterReviewedCheckbox(c.number, curPs);
      syncPgMarkAllReviewedButton(c.number, curPs);
      const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
      if (!state.activeFileByCl || !state.activeFileByCl[c.number]) {
        state.activeFileByCl = state.activeFileByCl || {};
        if (rawFiles.length > 0) {
          state.activeFileByCl[c.number] = rawFiles[0].file;
        }
      }
      if (state.activeFileByCl && state.activeFileByCl[c.number]) {
        updatePgActiveFile(c.number, state.activeFileByCl[c.number]);
      }
      if (!bundle || bundle._hasAnyImageFiles !== false) {
        for (let fIdx = 0; fIdx < rawFiles.length; fIdx++) {
          const f = rawFiles[fIdx];
          if (isPgImageFile(f.file) || f.is_image) {
            const safeFileId = String(f.file).replace(/[^a-zA-Z0-9_-]/g, "_");
            const status = getPgFileStatus(f);
            if (!isPgFileCollapsed(c.number, curPs, f.file)) {
              loadPgImageData(c.number, curPs, f.file, safeFileId, status);
            }
          }
        }
      }
    }
    syncSidebarLayoutDom();
    syncActiveActionButtons(commits, false);
    clearRenderPassCache();
    renderBatchReviewerPopoverDom();
    syncSwarmIndicatorsOnly({ commits, skipPanelRender: true, skipHarnessMenuSync: true });
    applyPgDiffPreferences();
    syncPgStickyFileHeaders();
    if (state.codeViewerMode) {
      syncCodeViewerModeDom({ restoreFileScroll: !opts.preserveScroll });
    }
  }

  function findInlineItemById(clNum, itemId) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    const list = state.inlineCommentsByCl[num] || [];
    const localHit = list.find(item => String(item.id) === idStr);
    if (localHit) return localHit;
    const bundle = state.diffsByCl[num];
    if (bundle && Array.isArray(bundle.unresolved_threads)) {
      for (let tIdx = 0; tIdx < bundle.unresolved_threads.length; tIdx++) {
        const srvItem = buildServerThreadInlineItem(num, bundle.unresolved_threads[tIdx], tIdx);
        if (srvItem.id === idStr) return srvItem;
      }
    }
    return null;
  }

  function collapseAllInlinePopups() {
    Object.keys(state.inlineCommentsByCl || {}).forEach(clKey => {
      (state.inlineCommentsByCl[clKey] || []).forEach(item => {
        item.collapsed = true;
      });
    });
    document.querySelectorAll(".diff-inline-comment").forEach(el => {
      el.classList.add("collapsed");
      const idAttr = el.getAttribute("data-inline-id") || "";
      if (idAttr.startsWith("srv-thread-")) {
        state.collapsedInlineIds.add(idAttr);
      }
      const btn = el.querySelector(".inline-collapse-btn");
      if (btn) btn.textContent = "Show ▾";
    });
  }

  function insertOrReplaceInlineItemInDom(clNum, item, opts = {}) {
    if (!item) return;
    const num = Number(clNum);
    if (!item.id) {
      item.id = `inline-${state.nextInlineItemId++}`;
    }
    const existingEl = document.getElementById(`inlineItem-${item.id}`);
    if (existingEl) {
      const hasOpenEditor = (
        state.editingQaItemById[item.id] !== undefined ||
        state.openFollowupByItemId[item.id] !== undefined ||
        state.openReplyByItemId[item.id] !== undefined ||
        (state.openDraftEditByItemId && state.openDraftEditByItemId[item.id] !== undefined)
      );
      if (hasOpenEditor && !opts.forceRender) {
        return;
      }
      existingEl.outerHTML = renderInlineCommentCardHtml(num, item);
      if (opts.focusTextareaId) {
        const ta = document.getElementById(opts.focusTextareaId);
        if (ta && typeof ta.focus === "function") {
          ta.focus();
          ta.selectionStart = ta.selectionEnd = ta.value.length;
        }
      }
      return;
    }
    const cardHtml = renderInlineCommentCardHtml(num, item);

    if (item.alt_id) {
      const altCardEl = document.getElementById(`aiAltCard-${num}-${item.alt_id}`);
      if (altCardEl) {
        let insertAfterAltEl = altCardEl;
        while (
          insertAfterAltEl.nextElementSibling &&
          insertAfterAltEl.nextElementSibling.classList &&
          insertAfterAltEl.nextElementSibling.classList.contains("diff-inline-comment")
        ) {
          insertAfterAltEl = insertAfterAltEl.nextElementSibling;
        }
        insertAfterAltEl.insertAdjacentHTML("afterend", cardHtml);
      }
      return;
    }

    const tables = Array.from(document.querySelectorAll(".code-table"));
    const tableEl = tables.find(
      t =>
        Number(t.getAttribute("data-cl")) === num &&
        (t.getAttribute("data-file") || "") === (item.file_path || "")
    ) || tables.find(t => (t.getAttribute("data-file") || "") === (item.file_path || ""));
    if (!tableEl) return;

    const allLines = Array.from(tableEl.querySelectorAll(".code-line[data-idx]"));
    if (item.start_idx != null && item.end_idx != null) {
      const indicatorCls = item.kind === "agent_question" ? "code-line-has-question" : "code-line-has-thread";
      allLines.forEach(lineEl => {
        const idx = Number(lineEl.getAttribute("data-idx"));
        if (!Number.isNaN(idx) && idx >= Number(item.start_idx) && idx <= Number(item.end_idx)) {
          lineEl.classList.add(indicatorCls);
        }
      });
    }

    let anchorLineEl = null;
    if (item.end_idx != null) {
      anchorLineEl = allLines.find(el => Number(el.getAttribute("data-idx")) === Number(item.end_idx)) || null;
    }
    if (!anchorLineEl && allLines.length > 0) {
      anchorLineEl = allLines.find(el => Number(el.getAttribute("data-new-ln")) === Number(item.line)) || allLines[allLines.length - 1];
    }
    if (!anchorLineEl) return;

    let insertAfterEl = anchorLineEl;
    while (
      insertAfterEl.nextElementSibling &&
      insertAfterEl.nextElementSibling.classList &&
      insertAfterEl.nextElementSibling.classList.contains("diff-inline-comment")
    ) {
      insertAfterEl = insertAfterEl.nextElementSibling;
    }
    insertAfterEl.insertAdjacentHTML("afterend", cardHtml);
  }

  function removeInlineQuestionCardFromDom(clNum, itemId) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    const existingList = state.inlineCommentsByCl[num] || [];
    const removedItem = existingList.find(it => String(it.id) === idStr);
    state.inlineCommentsByCl[num] = existingList.filter(it => String(it.id) !== idStr);
    delete state.editingQaItemById[idStr];
    delete state.openFollowupByItemId[idStr];
    state.replyNotifications = (state.replyNotifications || []).filter(n => String(n.itemId) !== idStr);
    renderAgentReplyBubbleStack();

    const cardEl = document.getElementById(`inlineItem-${idStr}`);
    if (cardEl) cardEl.remove();
    if (!removedItem) return;

    const tableEl = Array.from(document.querySelectorAll(".code-table")).find(
      t =>
        Number(t.getAttribute("data-cl")) === num &&
        (t.getAttribute("data-file") || "") === (removedItem.file_path || "")
    );
    if (!tableEl) return;
    const remainingQuestions = (state.inlineCommentsByCl[num] || []).filter(
      it => it.kind === "agent_question" && (it.file_path || "") === (removedItem.file_path || "")
    );
    tableEl.querySelectorAll(".code-line.code-line-has-question").forEach(lineEl => {
      const idx = Number(lineEl.getAttribute("data-idx"));
      const ln = Number(lineEl.getAttribute("data-new-ln") || lineEl.getAttribute("data-old-ln") || 0);
      const stillCovered = remainingQuestions.some(q => {
        if (q.start_idx != null && q.end_idx != null) {
          return idx >= Number(q.start_idx) && idx <= Number(q.end_idx);
        }
        return ln > 0 && ln >= Number(q.start_line || q.line || 1) && ln <= Number(q.end_line || q.line || 1);
      });
      if (!stillCovered) lineEl.classList.remove("code-line-has-question");
    });
  }

  function saveSelectionDraftAsCollapsedItem(prevSel) {
    if (!prevSel || prevSel.savedInlineId) return;
    const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
    if (inputEl) {
      prevSel.draft = inputEl.value;
      prevSel.commentDraft = inputEl.value;
      prevSel.askDraft = inputEl.value;
    }

    const draftMsg = (prevSel.draft || prevSel.commentDraft || prevSel.askDraft || "").trim();
    if (!draftMsg) return;
    const activeMode = prevSel.mode === "ask" ? "ask" : "comment";

    if (!state.inlineCommentsByCl[prevSel.clNum]) {
      state.inlineCommentsByCl[prevSel.clNum] = [];
    }
    const draftItem = {
      id: `inline-${state.nextInlineItemId++}`,
      kind: "draft",
      draft_mode: activeMode,
      author_name: (state.viewer || "you").split("@")[0],
      file_path: prevSel.filePath,
      line: prevSel.endLine,
      start_line: prevSel.startLine,
      end_line: prevSel.endLine,
      start_idx: prevSel.startIdx,
      end_idx: prevSel.endIdx,
      side: prevSel.side,
      alt_id: prevSel.altId || "",
      alt_title: prevSel.altTitle || "",
      unresolved: prevSel.unresolved !== false,
      message: draftMsg,
      code_snippet: prevSel.codeSnippet,
      collapsed: true,
    };
    prevSel.savedInlineId = draftItem.id;
    state.inlineCommentsByCl[prevSel.clNum].push(draftItem);
    insertOrReplaceInlineItemInDom(prevSel.clNum, draftItem);
  }

  function toggleInlineItemCollapse(clNum, itemId) {
    const idStr = String(itemId || "");
    const cardEl = document.getElementById(`inlineItem-${idStr}`);
    if (idStr.startsWith("srv-thread-")) {
      const nowCollapsed = !state.collapsedInlineIds.has(idStr);
      if (nowCollapsed) {
        state.collapsedInlineIds.add(idStr);
      } else {
        state.collapsedInlineIds.delete(idStr);
      }
      if (cardEl) {
        cardEl.classList.toggle("collapsed", nowCollapsed);
        const btn = (typeof cardEl.querySelector === "function") ? cardEl.querySelector(".inline-collapse-btn") : null;
        if (btn) btn.textContent = nowCollapsed ? "Show ▾" : "Hide ▴";
      }
      if (typeof syncPgAllCommentsCollapseButton === "function") {
        syncPgAllCommentsCollapseButton(clNum);
      }
      return;
    }

    const item = findInlineItemById(clNum, idStr);
    if (!item) return;
    item.collapsed = !item.collapsed;
    if (cardEl) {
      cardEl.classList.toggle("collapsed", Boolean(item.collapsed));
      const btn = (typeof cardEl.querySelector === "function") ? cardEl.querySelector(".inline-collapse-btn") : null;
      if (btn) btn.textContent = item.collapsed ? "Show ▾" : "Hide ▴";
    }
    if (typeof syncPgAllCommentsCollapseButton === "function") {
      syncPgAllCommentsCollapseButton(clNum);
    }
  }

  function copyInlineAgentPrompt(clNum, itemId) {
    const item = findInlineItemById(clNum, itemId);
    if (!item) return;
    const text = item.prompt || item.message || "";
    navigator.clipboard?.writeText(text);
    showToast("Copied agent question prompt to clipboard!");
  }

  function copyInlineAgentThread(clNum, itemId) {
    const item = findInlineItemById(clNum, itemId);
    if (!item) return;
    const turns = normalizeQuestionTurns(item);
    const startLn = item.start_line || item.line || 1;
    const endLn = item.end_line || item.line || startLn;
    const rangeLabel = startLn === endLn ? `L${startLn}` : `L${startLn}-${endLn}`;
    const participants = collectQuestionParticipants(item, turns);
    const headerLines = [
      `### 🤖 Shared Agent Q&A · \`ag/${clNum}\` · \`${item.file_path || "/COMMIT_MSG"}:${rangeLabel}\``,
      participants.length > 0 ? `Participants: ${participants.map(p => `@${p}`).join(", ")}` : "",
      item.code_snippet ? `\`\`\`\n${item.code_snippet}\n\`\`\`` : "",
    ].filter(Boolean);
    const turnBlocks = turns.map((t, idx) => {
      const label = idx === 0 ? "Question" : `Follow-up #${idx}`;
      const authorTag = t.author ? ` (@${t.author})` : "";
      const replyBlock = t.reply ? `\n\n**✨ Jetski Agent:**\n${t.reply}` : "\n\n*(⏳ Waiting for agent reply…)*";
      return `**${label}${authorTag}:** ${t.question}${replyBlock}`;
    });
    const markdown = `${headerLines.join("\n\n")}\n\n---\n\n${turnBlocks.join("\n\n---\n\n")}`;
    navigator.clipboard?.writeText(markdown);
    showToast(`📋 Copied ${turns.length}-turn Q&A thread to clipboard!`);
  }

  function discardInlineDraft(clNum, itemId) {
    const num = Number(clNum);
    const list = state.inlineCommentsByCl[num] || [];
    state.inlineCommentsByCl[num] = list.filter(x => String(x.id) !== String(itemId));
    const el = document.getElementById(`inlineItem-${itemId}`);
    if (el && typeof el.remove === "function") el.remove();
    updateReplyButtonDraftsBadge(num);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    showToast("Discarded saved draft.");
  }

  function reopenInlineBlockInComposer(clNum, itemId, mode = "ask", consumeDraft = false) {
    const num = Number(clNum);
    const item = findInlineItemById(num, itemId);
    if (!item) return;

    const draftText = consumeDraft ? (item.message || "") : "";
    if (consumeDraft && item.kind === "draft") {
      state.inlineCommentsByCl[num] = (state.inlineCommentsByCl[num] || []).filter(
        x => String(x.id) !== String(itemId)
      );
      const draftEl = document.getElementById(`inlineItem-${itemId}`);
      if (draftEl && typeof draftEl.remove === "function") draftEl.remove();
    }

    if (item.alt_id) {
      openAiAlternativeInComposer(num, item.alt_id, mode, draftText || null);
      return;
    }

    const tables = Array.from(document.querySelectorAll(".code-table"));
    const tableEl = tables.find(
      t =>
        Number(t.getAttribute("data-cl")) === num &&
        (t.getAttribute("data-file") || "") === (item.file_path || "")
    ) || tables.find(t => (t.getAttribute("data-file") || "") === (item.file_path || ""));
    if (!tableEl) return;

    const allLines = Array.from(tableEl.querySelectorAll(".code-line[data-idx]"));
    let startIdx = item.start_idx != null ? Number(item.start_idx) : 0;
    let endIdx = item.end_idx != null ? Number(item.end_idx) : startIdx;
    if (item.start_idx == null && allLines.length > 0) {
      const matchedLine = allLines.find(el => Number(el.getAttribute("data-new-ln")) === Number(item.line));
      if (matchedLine) {
        startIdx = Number(matchedLine.getAttribute("data-idx") || 0);
        endIdx = startIdx;
      }
    }

    selectCodeBlockRange(tableEl, startIdx, endIdx, {
      anchorIdx: startIdx,
      mode,
      focusInput: true,
      initialDraft: draftText,
    });
  }

  function deselectBrowserSelection() {
    const doClear = () => {
      try {
        if (typeof document !== "undefined" && document.activeElement) {
          const act = document.activeElement;
          if (act && typeof act.setSelectionRange === "function") {
            try { act.setSelectionRange(0, 0); } catch (_) {}
          }
        }
      } catch (_) {}
      try {
        if (typeof window !== "undefined" && window.getSelection) {
          const sel = window.getSelection();
          if (sel) {
            try {
              if (typeof sel.removeAllRanges === "function") sel.removeAllRanges();
            } catch (_) {}
            try {
              if (typeof sel.empty === "function") sel.empty();
            } catch (_) {}
            try {
              while (sel.rangeCount > 0) {
                sel.removeRange(sel.getRangeAt(0));
              }
            } catch (_) {}
          }
        }
      } catch (_) {}
      try {
        if (typeof document !== "undefined") {
          if (typeof document.getSelection === "function") {
            const docSel = document.getSelection();
            if (docSel) {
              try { if (typeof docSel.removeAllRanges === "function") docSel.removeAllRanges(); } catch (_) {}
              try { if (typeof docSel.empty === "function") docSel.empty(); } catch (_) {}
              try {
                while (docSel.rangeCount > 0) {
                  docSel.removeRange(docSel.getRangeAt(0));
                }
              } catch (_) {}
            }
          }
          if (document.selection && typeof document.selection.empty === "function") {
            document.selection.empty();
          }
        }
      } catch (_) {}
    };

    doClear();
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(doClear);
    }
    if (typeof setTimeout === "function") {
      setTimeout(doClear, 0);
      setTimeout(doClear, 20);
      setTimeout(doClear, 60);
      setTimeout(doClear, 150);
    }
  }

  function clearCodeSelection(opts = {}) {
    if (state.codeSelection) {
      if (!opts || !opts.discardDraft) {
        saveSelectionDraftAsCollapsedItem(state.codeSelection);
      }
    }
    state.codeSelection = null;
    if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") {
      document.querySelectorAll(".code-line.code-line-selected, .code-line.is-drag-preview, .code-line-selected, .is-drag-preview").forEach(el => {
        el.classList.remove("code-line-selected", "is-drag-preview");
      });
    }
    const existingComposer = (typeof document !== "undefined" && typeof document.getElementById === "function")
      ? document.getElementById("codeSelectionComposer")
      : null;
    if (existingComposer && typeof existingComposer.remove === "function") {
      existingComposer.remove();
    }
    deselectBrowserSelection();
  }

  function closeCodeSelectionComposer(event, opts = {}) {
    if (state.composerAutosaveTimer) {
      clearTimeout(state.composerAutosaveTimer);
      state.composerAutosaveTimer = null;
    }
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    if (typeof document !== "undefined" && document.activeElement) {
      const activeEl = document.activeElement;
      if (activeEl && (activeEl.closest ? activeEl.closest("#codeSelectionComposer") : (activeEl.id === "codeBlockCommentInput" || activeEl.id === "codeBlockAskInput"))) {
        try {
          if (typeof activeEl.setSelectionRange === "function") {
            activeEl.setSelectionRange(0, 0);
          }
          activeEl.blur();
        } catch (_) {}
      }
    }
    const discardDraft = (opts && opts.discardDraft !== undefined) ? Boolean(opts.discardDraft) : true;
    clearCodeSelection({ ...opts, discardDraft });
    deselectBrowserSelection();
  }

  function buildCodeSelectionComposerInnerHtml(sel) {
    if (!sel) return "";
    const isAltCard = Boolean(sel.altId);
    const isCommitMsg = !isAltCard && sel.filePath === "/COMMIT_MSG";
    const activeMode = (sel.mode === "ask") ? "ask" : "comment";
    const rangeLabel = isAltCard
      ? String(sel.altTitle || sel.altId)
      : (sel.startLine === sel.endLine
        ? `L${sel.startLine}`
        : `L${sel.startLine}–L${sel.endLine}`);
    const lineCountLabel = sel.lineCount === 1 ? "1 line" : `${sel.lineCount} lines`;
    const sideBadge = sel.side === "PARENT" ? " · Base/Parent" : "";
    const sharedDraft = escapeHtml(sel.draft ?? (activeMode === "ask" ? sel.askDraft : sel.commentDraft) ?? "");
    const unresolvedChecked = sel.unresolved !== false ? "checked" : "";
    const targetLabel = isAltCard
      ? `Alternative (${sel.altTitle || sel.altId})`
      : (isCommitMsg ? "Commit Message (/COMMIT_MSG)" : sel.filePath);

    const askQuickChipsHtml = isAltCard
      ? `<div class="composer-quick-chips">
          <span style="font-size:11px;color:var(--g-muted);font-weight:600;">Quick prompts:</span>
          <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Check the actual codebase to see how much code/plumbing this alternative would require compared to the CL.')">Compare LOC &amp; complexity</button>
          <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Inspect existing callers and interfaces in the repo to verify if this alternative fits cleanly without breaking invariants.')">Check callers &amp; interfaces</button>
          <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('What subtle edge cases, regressions, or performance pitfalls could this alternative introduce?')">Check edge cases</button>
          <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Draft a short, constructive Gerrit review question asking the author whether they considered this alternative.')">Draft question for author</button>
        </div>`
      : (isCommitMsg
        ? `<div class="composer-quick-chips">
            <span style="font-size:11px;color:var(--g-muted);font-weight:600;">Quick prompts:</span>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Check whether this commit message follows Semantic Commit formatting, line length limits, and required Bug:/Test:/Change-Id: footers.')">Validate Semantic Commit format</button>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Does this commit message accurately and completely describe the code changes in the CL?')">Does message match diff?</button>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Suggest a clearer, more concise commit message for this CL.')">Suggest clearer rewrite</button>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Explain the background and motivation described in this commit message.')">Explain context</button>
          </div>`
        : `<div class="composer-quick-chips">
            <span style="font-size:11px;color:var(--g-muted);font-weight:600;">Quick prompts:</span>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Explain what this highlighted code block does and how it fits into the CL.')">Explain this block</button>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Check this highlighted code block for bugs, race conditions, lifetime issues, or edge cases.')">Check for bugs / edge cases</button>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Is there an existing helper in //emulator/libs/* or a cleaner pattern we should reuse here?')">Check library reuse</button>
            <button type="button" class="composer-quick-chip" onclick="applyQuickCodeQuestion('Can this code block be simplified or made more readable?')">Suggest simplification</button>
          </div>`);

    const commentQuickChipsHtml = `<div class="composer-quick-chips">
      <span style="font-size:11px;color:var(--g-muted);font-weight:600;">Quick responses:</span>
      <button type="button" class="composer-quick-chip" onclick="applyQuickReviewComment('Done.')" title="Insert Done response">Done</button>
      <button type="button" class="composer-quick-chip" onclick="applyQuickReviewComment('Acknowledged.')" title="Insert Ack response">Ack</button>
      <button type="button" class="composer-quick-chip" onclick="applyQuickReviewComment('Please add unit test coverage for this case.')" title="Request unit test coverage">Add test coverage</button>
      <button type="button" class="composer-quick-chip" onclick="applyQuickReviewComment('Can we reuse an existing utility from //emulator/libs/* here?')" title="Suggest //emulator/libs/* reuse">Reuse //emulator/libs/*</button>
      <button type="button" class="composer-quick-chip" onclick="applyQuickReviewComment('Please format error message per go/error-messages with actionability.')" title="Suggest go/error-messages compliance">go/error-messages</button>
    </div>`;

    const quickChipsHtml = activeMode === "ask" ? askQuickChipsHtml : commentQuickChipsHtml;

    const headerLocHtml = isAltCard
      ? `<div class="composer-loc-badge">
          <span>Alternative Solution · <strong>ag/${sel.clNum}</strong></span>
          <span>·</span>
          <code>${escapeHtml(sel.altTitle || sel.altId)}</code>
        </div>`
      : `<div class="composer-loc-badge">
          <span>${isCommitMsg ? "Selected Commit Message" : "Selected Block"} · <strong>ag/${sel.clNum}</strong></span>
          <span>·</span>
          <code>${isCommitMsg ? escapeHtml(sel.filePath) : renderCodeSearchFileLinkHtml(sel.clNum, sel.filePath, sel.filePath, sel.startLine)}</code>
          <span class="composer-loc-range">${escapeHtml(rangeLabel)}${escapeHtml(sideBadge)} (${lineCountLabel})</span>
        </div>`;

    const placeholderText = activeMode === "ask"
      ? `Ask Jetski agent about ${targetLabel}${isAltCard ? "" : ` (${rangeLabel})`}… (Ctrl+Enter to ask, Tab to toggle mode)`
      : `Write a Gerrit review comment on ${targetLabel}${isAltCard ? "" : ` (${rangeLabel})`} — Markdown supported… (Ctrl+Enter to save draft, Shift+Ctrl+Enter to post, Tab to toggle mode)`;

    const composerSaveStatus = (sel && sel.saveStatus) || (sel && sel.draft ? "saved" : "saved");
    const composerAutosaveCls = `is-${composerSaveStatus}`;
    const composerAutosaveText = composerSaveStatus === "unsaved" ? "● Unsaved" : (composerSaveStatus === "saving" ? "Saving…" : "✓ Saved");
    const composerAutosaveTitle = composerSaveStatus === "unsaved"
      ? "Unsaved draft edits (autosaves after pause or on blur)"
      : (composerSaveStatus === "saving" ? "Autosaving draft comment…" : "Draft changes automatically saved");

    const footerHtml = activeMode === "ask"
      ? `<div class="composer-footer">
          <span style="font-size:11.5px;color:var(--g-muted);">🤖 Jetski Code Intelligence</span>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <button id="btnCancelCodeAsk" type="button" class="btn btn-subtle" onmousedown="event.stopPropagation(); deselectBrowserSelection();" onmouseup="event.stopPropagation(); deselectBrowserSelection();" onclick="closeCodeSelectionComposer(event, { discardDraft: true })" title="Cancel and deselect (Esc)">✕ Cancel</button>
            <button id="btnSubmitCodeAsk" type="button" class="btn btn-swarm" onclick="submitSelectedCodeQuestion(this)" title="Ask Jetski agent (Ctrl+Enter)">🤖 Ask Agent</button>
            <button type="button" class="btn btn-subtle" onclick="setCodeSelectionMode('comment', { fromKeyboard: true })" title="Switch to Review Comment mode (Alt+1, Alt+M, or Tab)">💬 Switch to Comment (Alt+1)</button>
          </div>
        </div>`
      : `<div class="composer-footer">
          <label class="composer-checkbox-label" title="Unresolved comment thread requires resolution before submit">
            <input id="codeBlockUnresolvedChk" type="checkbox" ${unresolvedChecked} onchange="if(state.codeSelection) state.codeSelection.unresolved = this.checked;" />
            <span>Mark as Unresolved</span>
          </label>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span id="composerAutosaveBadge" class="pg-draft-autosave-badge ${composerAutosaveCls}" title="${escapeHtml(composerAutosaveTitle)}">${composerAutosaveText}</span>
            <button id="btnCancelCodeComment" type="button" class="btn btn-subtle" onmousedown="event.stopPropagation(); deselectBrowserSelection();" onmouseup="event.stopPropagation(); deselectBrowserSelection();" onclick="closeCodeSelectionComposer(event, { discardDraft: true })" title="Cancel and deselect (Esc)">✕ Cancel</button>
            <button id="btnSaveCodeDraft" type="button" class="btn btn-save-draft" onclick="saveSelectionDraftFromComposer()" title="Save draft comment without posting (Ctrl+Enter)">💾 Save Draft</button>
            <button id="btnSubmitCodeComment" type="button" class="btn btn-approve" onclick="submitSelectedCodeComment(this)" title="Post comment to Gerrit (notify: OWNER · Shift+Ctrl+Enter)">💬 Post Comment (notify: OWNER)</button>
            <button type="button" class="btn btn-subtle" onclick="setCodeSelectionMode('ask', { fromKeyboard: true })" title="Switch to Ask Agent mode (Alt+2, Alt+M, or Tab)">🤖 Switch to Ask Agent (Alt+2)</button>
          </div>
        </div>`;

    return `<div class="composer-header">
      ${headerLocHtml}
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <div class="composer-mode-switch" role="tablist" aria-label="Composer Mode">
          <button
            type="button"
            id="btnComposerTabComment"
            class="composer-mode-tab ${activeMode === "comment" ? "active" : ""}"
            role="tab"
            aria-selected="${activeMode === "comment" ? "true" : "false"}"
            tabindex="${activeMode === "comment" ? "0" : "-1"}"
            aria-controls="codeBlockCommentInput"
            onclick="setCodeSelectionMode('comment')"
            onkeydown="onComposerModeTabKeydown(event, 'comment')"
            title="Write review comment (Alt+1, Alt+M, or Tab)"
          >💬 Comment</button>
          <button
            type="button"
            id="btnComposerTabAsk"
            class="composer-mode-tab ${activeMode === "ask" ? "active" : ""}"
            role="tab"
            aria-selected="${activeMode === "ask" ? "true" : "false"}"
            tabindex="${activeMode === "ask" ? "0" : "-1"}"
            aria-controls="codeBlockCommentInput"
            onclick="setCodeSelectionMode('ask')"
            onkeydown="onComposerModeTabKeydown(event, 'ask')"
            title="Ask Jetski AI Agent about code (Alt+2, Alt+M, or Tab)"
          >🤖 Ask Agent</button>
        </div>
        <button type="button" class="commit-msg-btn" onclick="copySelectedCodeSnippet()">${(isCommitMsg || isAltCard) ? "Copy Text" : "Copy Code"}</button>
        <button type="button" class="commit-msg-btn btn-copy-permalink" onclick="copySelectedCodePermalink()" title="Copy permalink URL for this selected line range (Shortcut: y)">🔗 Copy Link</button>
        <button type="button" class="commit-msg-btn btn-copy-markdown" onclick="copySelectedCodeMarkdown()" title="Copy formatted Markdown quote snippet with permalink link">📋 Copy Markdown</button>
        <button type="button" id="btnCloseCodeSelectionComposer" class="commit-msg-btn btn-close-code-composer" onmousedown="event.stopPropagation(); deselectBrowserSelection();" onmouseup="event.stopPropagation(); deselectBrowserSelection();" onclick="closeCodeSelectionComposer(event, { discardDraft: true })" title="Close composer and deselect (Esc)">✕ Close</button>
      </div>
    </div>
    <div class="composer-body">
      ${quickChipsHtml}
      <textarea id="codeBlockCommentInput" class="composer-textarea" placeholder="${escapeHtml(placeholderText)}" oninput="onCodeComposerInput('${activeMode}', this.value)" onblur="flushPgComposerDraftAutosave()" onkeydown="onCodeComposerKeydown(event, state.codeSelection ? state.codeSelection.mode : 'comment')">${sharedDraft}</textarea>
      ${footerHtml}
    </div>`;
  }

  function extractSelectedBlockMetadata(tableEl, selectedEls, preferredSide = null) {
    const lastEl = selectedEls[selectedEls.length - 1];
    const clNum = Number(lastEl.getAttribute("data-cl") || tableEl.getAttribute("data-cl") || state.selectedClNumber || 0);
    const filePath = lastEl.getAttribute("data-file") || tableEl.getAttribute("data-file") || "";
    const isCommitMsg = filePath === "/COMMIT_MSG";
    let side = "REVISION";
    if (preferredSide === "PARENT" || preferredSide === "REVISION") {
      side = preferredSide;
    } else {
      side = selectedEls.some(el => (el.getAttribute("data-new-ln") || "").trim() !== "") ? "REVISION" : "PARENT";
    }
    const lineNums = [];
    selectedEls.forEach(el => {
      const startAttr = Number(el.getAttribute("data-start-ln") || 0);
      if (!Number.isNaN(startAttr) && startAttr > 0) lineNums.push(startAttr);
      const rawLn = side === "PARENT"
        ? (el.getAttribute("data-old-ln") || el.getAttribute("data-new-ln") || "")
        : (el.getAttribute("data-new-ln") || el.getAttribute("data-old-ln") || "");
      const n = Number(rawLn);
      if (!Number.isNaN(n) && n > 0) lineNums.push(n);
    });
    const startLine = lineNums.length > 0 ? Math.min(...lineNums) : 1;
    const endLine = lineNums.length > 0 ? Math.max(...lineNums) : startLine;
    const snippetLines = selectedEls.map(el => {
      if (isCommitMsg) return el.getAttribute("data-raw-text") ?? (el.querySelector(".code-text")?.textContent || "");
      if (el.classList.contains("code-line-sbs")) {
        const sideEl = side === "PARENT" ? el.querySelector(".code-side-left") : el.querySelector(".code-side-right");
        const tp = side === "PARENT"
          ? (el.querySelector(".code-side-left.side-del") ? "del" : "ctx")
          : (el.querySelector(".code-side-right.side-add") ? "add" : "ctx");
        const prefix = tp === "add" ? "+" : (tp === "del" ? "-" : " ");
        const text = (sideEl ? sideEl.textContent : "") || (el.querySelector(".code-text")?.textContent || "");
        return prefix + text;
      }
      const tp = el.getAttribute("data-type") || "ctx";
      const prefix = tp === "add" ? "+" : (tp === "del" ? "-" : " ");
      return prefix + (el.querySelector(".code-text")?.textContent || "");
    });
    const lineCount = isCommitMsg ? Math.max(1, endLine - startLine + 1) : selectedEls.length;
    return { clNum, filePath, side, startLine, endLine, lineCount, codeSnippet: snippetLines.join("\n") };
  }

  function mountCodeSelectionComposer(lastEl, focusInput = false) {
    let composerEl = document.getElementById("codeSelectionComposer");
    if (!composerEl) {
      composerEl = document.createElement("div");
      composerEl.id = "codeSelectionComposer";
      composerEl.className = "code-selection-composer";
    }
    composerEl.innerHTML = buildCodeSelectionComposerInnerHtml(state.codeSelection);
    lastEl.insertAdjacentElement("afterend", composerEl);
    if (typeof composerEl.scrollIntoView === "function") {
      composerEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    if (focusInput) {
      const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
      if (inputEl) {
        inputEl.focus();
        try {
          const len = inputEl.value.length;
          inputEl.setSelectionRange(len, len);
        } catch (_) {}
      }
    }
  }

  function selectCodeBlockRange(tableEl, rawStartIdx, rawEndIdx, opts = {}) {
    if (!tableEl) return;
    const minIdx = Math.min(Number(rawStartIdx), Number(rawEndIdx));
    const maxIdx = Math.max(Number(rawStartIdx), Number(rawEndIdx));
    const selectedEls = Array.from(tableEl.querySelectorAll(".code-line[data-idx]")).filter(el => {
      const idx = Number(el.getAttribute("data-idx"));
      return !Number.isNaN(idx) && idx >= minIdx && idx <= maxIdx;
    });
    if (selectedEls.length === 0) return;

    document.querySelectorAll(".code-line.code-line-selected").forEach(el => el.classList.remove("code-line-selected"));
    selectedEls.forEach(el => el.classList.add("code-line-selected"));

    const meta = extractSelectedBlockMetadata(tableEl, selectedEls, opts.side);
    const prevSel = state.codeSelection;
    const isSameBlock = Boolean(
      prevSel && prevSel.clNum === meta.clNum && prevSel.filePath === meta.filePath &&
      prevSel.startIdx === minIdx && prevSel.endIdx === maxIdx &&
      (!opts.side || prevSel.side === meta.side)
    );
    if (!isSameBlock) {
      if (prevSel) saveSelectionDraftAsCollapsedItem(prevSel);
      collapseAllInlinePopups();
    }

    const resolvedMode = opts.mode || (prevSel && prevSel.mode) || "comment";
    const initialDraft = typeof opts.initialDraft === "string" ? opts.initialDraft : null;
    const prevSharedDraft = isSameBlock ? (prevSel.draft || prevSel.commentDraft || prevSel.askDraft || "") : "";
    const resolvedDraft = initialDraft !== null ? initialDraft : prevSharedDraft;
    state.codeSelection = {
      ...meta,
      tableEl,
      anchorIdx: opts.anchorIdx != null ? Number(opts.anchorIdx) : minIdx,
      headIdx: opts.headIdx != null ? Number(opts.headIdx) : maxIdx,
      startIdx: minIdx,
      endIdx: maxIdx,
      mode: resolvedMode,
      draft: resolvedDraft,
      commentDraft: resolvedDraft,
      askDraft: resolvedDraft,
      unresolved: isSameBlock && prevSel.unresolved !== undefined ? prevSel.unresolved : true,
      sentPrompt: "",
      savedInlineId: null,
    };
    if (opts.mountComposer !== false) {
      mountCodeSelectionComposer(selectedEls[selectedEls.length - 1], Boolean(opts.focusInput));
    }
  }

  function isPgDiffNavContext() {
    if (typeof document === "undefined") return false;
    const activeEl = document.activeElement;
    if (activeEl && typeof activeEl.closest === "function") {
      if (activeEl.tagName === "INPUT" || activeEl.tagName === "TEXTAREA" || activeEl.tagName === "SELECT") {
        return false;
      }
      if (activeEl.closest(".pg-file-list-table, .pg-file-row, .pg-dir-row, .queue-panel, #queueList, .cl-card, .pg-dashboard-row, .pg-batch-actions-strip")) {
        return false;
      }
      if (activeEl.closest(".code-table, .pg-file-diff-block, .pg-expanded-ctx-row, .diff-pane, #diffPane, #diffScroll, .code-selection-composer")) {
        return true;
      }
    }
    if (state.codeSelection && state.codeSelection.filePath) {
      return true;
    }
    if (state.activeLine && document.querySelector(".code-line.pg-line-focused")) {
      return true;
    }
    return false;
  }

  function navigatePgDiffLine(delta = 1, clNum = null) {
    if (typeof document === "undefined") return false;
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);

    if (state.codeSelection) {
      clearCodeSelection({ discardDraft: true });
    }

    let curLineEl = document.querySelector(".code-line.pg-line-focused[data-idx]");
    if (!curLineEl && state.activeLine && state.activeLine.filePath) {
      curLineEl = document.querySelector(
        `.code-line[data-file="${state.activeLine.filePath}"][data-new-ln="${state.activeLine.lineNum}"], ` +
        `.code-line[data-file="${state.activeLine.filePath}"][data-old-ln="${state.activeLine.lineNum}"]`
      );
    }
    if (!curLineEl && document.activeElement && typeof document.activeElement.closest === "function") {
      curLineEl = document.activeElement.closest(".code-line[data-idx]");
    }

    if (!curLineEl) {
      const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
      const targetBlock = activeBlock ||
        document.querySelector(`.pg-file-diff-block[data-cl="${num}"]:not(.is-collapsed)`) ||
        document.querySelector(".pg-file-diff-block:not(.is-collapsed)") ||
        document.querySelector(`.pg-file-diff-block[data-cl="${num}"]`) ||
        document.querySelector(".pg-file-diff-block");
      if (!targetBlock) return false;
      const lines = Array.from(targetBlock.querySelectorAll(".code-line[data-idx]"));
      if (lines.length === 0) return false;
      curLineEl = delta > 0 ? lines[0] : lines[lines.length - 1];
      const targetIdx = Number(curLineEl.getAttribute("data-idx"));
      const isBase = false;
      setActivePgLineElement(curLineEl, isBase);
      const filePath = curLineEl.getAttribute("data-file") || targetBlock.getAttribute("data-file") || "";
      const ln = curLineEl.getAttribute("data-new-ln") || curLineEl.getAttribute("data-old-ln") || (targetIdx + 1);
      state.activeLine = { clNum: num, filePath, lineNum: Number(ln), isBase, side: "REVISION" };
      syncPgDiffLineHash(filePath, ln, isBase);
      if (typeof curLineEl.scrollIntoView === "function") {
        curLineEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      return true;
    }

    const tableEl = curLineEl.closest(".code-table");
    if (!tableEl) return false;
    const lines = Array.from(tableEl.querySelectorAll(".code-line[data-idx]"));
    if (lines.length === 0) return false;
    const curPos = lines.indexOf(curLineEl);
    const targetPos = curPos + delta;

    if (targetPos >= 0 && targetPos < lines.length) {
      const nextLineEl = lines[targetPos];
      const isBase = state.activeLine ? Boolean(state.activeLine.isBase) : false;
      setActivePgLineElement(nextLineEl, isBase);
      const targetIdx = Number(nextLineEl.getAttribute("data-idx"));
      const filePath = nextLineEl.getAttribute("data-file") || tableEl.getAttribute("data-file") || "";
      const ln = isBase
        ? (nextLineEl.getAttribute("data-old-ln") || nextLineEl.getAttribute("data-new-ln") || (targetIdx + 1))
        : (nextLineEl.getAttribute("data-new-ln") || nextLineEl.getAttribute("data-old-ln") || (targetIdx + 1));
      state.activeLine = { clNum: num, filePath, lineNum: Number(ln), isBase, side: isBase ? "PARENT" : "REVISION" };
      syncPgDiffLineHash(filePath, ln, isBase);
      if (typeof nextLineEl.scrollIntoView === "function") {
        nextLineEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      return true;
    }

    // Step to adjacent visible diff block
    const allBlocks = Array.from(document.querySelectorAll(".pg-file-diff-block:not(.is-collapsed)"));
    const curBlock = curLineEl.closest(".pg-file-diff-block");
    const blockIdx = allBlocks.indexOf(curBlock);
    if (blockIdx !== -1) {
      const nextBlock = delta > 0 ? allBlocks[blockIdx + 1] : allBlocks[blockIdx - 1];
      if (nextBlock) {
        const nextLines = Array.from(nextBlock.querySelectorAll(".code-line[data-idx]"));
        if (nextLines.length > 0) {
          const nextLineEl = delta > 0 ? nextLines[0] : nextLines[nextLines.length - 1];
          const isBase = state.activeLine ? Boolean(state.activeLine.isBase) : false;
          setActivePgLineElement(nextLineEl, isBase);
          const targetIdx = Number(nextLineEl.getAttribute("data-idx"));
          const filePath = nextLineEl.getAttribute("data-file") || nextBlock.getAttribute("data-file") || "";
          const ln = isBase
            ? (nextLineEl.getAttribute("data-old-ln") || nextLineEl.getAttribute("data-new-ln") || (targetIdx + 1))
            : (nextLineEl.getAttribute("data-new-ln") || nextLineEl.getAttribute("data-old-ln") || (targetIdx + 1));
          state.activeLine = { clNum: num, filePath, lineNum: Number(ln), isBase, side: isBase ? "PARENT" : "REVISION" };
          syncPgDiffLineHash(filePath, ln, isBase);
          if (typeof nextLineEl.scrollIntoView === "function") {
            nextLineEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
          return true;
        }
      }
    }
    return false;
  }

  function stepPgDiffLineSelection(delta = 1, clNum = null) {
    if (typeof document === "undefined") return false;
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);

    let tableEl = null;
    let anchorIdx = null;
    let currentHeadIdx = null;
    let side = null;

    if (state.codeSelection) {
      tableEl = state.codeSelection.tableEl ||
        document.querySelector(`.pg-file-diff-block[data-file="${state.codeSelection.filePath}"] .code-table`) ||
        document.querySelector(".code-table");
      if (!tableEl) return false;
      anchorIdx = (typeof state.codeSelection.anchorIdx === "number") ? state.codeSelection.anchorIdx : state.codeSelection.startIdx;
      currentHeadIdx = (typeof state.codeSelection.headIdx === "number")
        ? state.codeSelection.headIdx
        : (state.codeSelection.endIdx !== anchorIdx ? state.codeSelection.endIdx : state.codeSelection.startIdx);
      side = state.codeSelection.side || null;
    } else {
      let curLineEl = document.querySelector(".code-line.pg-line-focused[data-idx]");
      if (!curLineEl && state.activeLine && state.activeLine.filePath) {
        curLineEl = document.querySelector(
          `.code-line[data-file="${state.activeLine.filePath}"][data-new-ln="${state.activeLine.lineNum}"], ` +
          `.code-line[data-file="${state.activeLine.filePath}"][data-old-ln="${state.activeLine.lineNum}"]`
        );
      }
      if (!curLineEl && document.activeElement && typeof document.activeElement.closest === "function") {
        curLineEl = document.activeElement.closest(".code-line[data-idx]");
      }
      if (!curLineEl) {
        const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
        const targetBlock = activeBlock ||
          document.querySelector(`.pg-file-diff-block[data-cl="${num}"]:not(.is-collapsed)`) ||
          document.querySelector(".pg-file-diff-block:not(.is-collapsed)") ||
          document.querySelector(`.pg-file-diff-block[data-cl="${num}"]`) ||
          document.querySelector(".pg-file-diff-block");
        if (!targetBlock) return false;
        const lines = Array.from(targetBlock.querySelectorAll(".code-line[data-idx]"));
        if (lines.length === 0) return false;
        curLineEl = lines[0];
      }
      tableEl = curLineEl.closest(".code-table");
      if (!tableEl) return false;
      anchorIdx = Number(curLineEl.getAttribute("data-idx"));
      if (Number.isNaN(anchorIdx)) return false;
      currentHeadIdx = anchorIdx;
      side = state.activeLine && state.activeLine.isBase ? "PARENT" : "REVISION";
    }

    const lines = Array.from(tableEl.querySelectorAll(".code-line[data-idx]"));
    if (lines.length === 0) return false;
    const maxIdx = Number(lines[lines.length - 1].getAttribute("data-idx") || (lines.length - 1));
    const newHeadIdx = Math.max(0, Math.min(maxIdx, currentHeadIdx + delta));

    selectCodeBlockRange(tableEl, anchorIdx, newHeadIdx, {
      anchorIdx,
      headIdx: newHeadIdx,
      mountComposer: false,
      focusInput: false,
      mode: (state.codeSelection && state.codeSelection.mode) || "comment",
      side,
    });

    const headLineEl = tableEl.querySelector(`.code-line[data-idx="${newHeadIdx}"]`);
    if (headLineEl && typeof headLineEl.scrollIntoView === "function") {
      headLineEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    const minI = Math.min(anchorIdx, newHeadIdx);
    const maxI = Math.max(anchorIdx, newHeadIdx);
    const startLineEl = tableEl.querySelector(`.code-line[data-idx="${minI}"]`);
    const endLineEl = tableEl.querySelector(`.code-line[data-idx="${maxI}"]`);
    const sLn = startLineEl ? (startLineEl.getAttribute("data-new-ln") || startLineEl.getAttribute("data-old-ln") || (minI + 1)) : (minI + 1);
    const eLn = endLineEl ? (endLineEl.getAttribute("data-new-ln") || endLineEl.getAttribute("data-old-ln") || (maxI + 1)) : (maxI + 1);
    const rangeText = (sLn === eLn) ? `Line L${sLn}` : `Lines L${sLn}–L${eLn}`;
    if (typeof showToast === "function") {
      showToast(`Selected ${rangeText} · Press 'c' to comment · Esc to cancel`, 1500);
    }
    return true;
  }

  function switchPgDiffSide(targetSide = null, clNum = null) {
    if (typeof document === "undefined") return false;
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);

    if (state.codeSelection) {
      clearCodeSelection({ discardDraft: true });
    }

    let curLineEl = document.querySelector(".code-line.pg-line-focused[data-idx]");
    if (!curLineEl && state.activeLine && state.activeLine.filePath) {
      curLineEl = document.querySelector(
        `.code-line[data-file="${state.activeLine.filePath}"][data-new-ln="${state.activeLine.lineNum}"], ` +
        `.code-line[data-file="${state.activeLine.filePath}"][data-old-ln="${state.activeLine.lineNum}"]`
      );
    }
    if (!curLineEl && document.activeElement && typeof document.activeElement.closest === "function") {
      curLineEl = document.activeElement.closest(".code-line[data-idx]");
    }
    if (!curLineEl) {
      const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
      const targetBlock = activeBlock ||
        document.querySelector(`.pg-file-diff-block[data-cl="${num}"]:not(.is-collapsed)`) ||
        document.querySelector(".pg-file-diff-block:not(.is-collapsed)") ||
        document.querySelector(`.pg-file-diff-block[data-cl="${num}"]`) ||
        document.querySelector(".pg-file-diff-block");
      if (!targetBlock) return false;
      const lines = Array.from(targetBlock.querySelectorAll(".code-line[data-idx]"));
      if (lines.length === 0) return false;
      curLineEl = lines[0];
    }

    const curIsBase = state.activeLine ? Boolean(state.activeLine.isBase) : false;
    let wantsBase = false;
    if (targetSide === "left" || targetSide === "base" || targetSide === "parent" || targetSide === "a" || targetSide === true) {
      wantsBase = true;
    } else if (targetSide === "right" || targetSide === "revision" || targetSide === "b" || targetSide === false) {
      wantsBase = false;
    } else {
      wantsBase = !curIsBase;
    }

    let targetLineEl = curLineEl;
    let targetLn = wantsBase ? targetLineEl.getAttribute("data-old-ln") : targetLineEl.getAttribute("data-new-ln");

    // If active row lacks target side in SBS (e.g. pure addition or deletion), search adjacent rows in same table
    if (!targetLn) {
      const tableEl = curLineEl.closest(".code-table");
      if (tableEl) {
        const rows = Array.from(tableEl.querySelectorAll(".code-line[data-idx]"));
        const curIdx = rows.indexOf(curLineEl);
        if (curIdx !== -1) {
          for (let step = 1; step < rows.length; step++) {
            const nextRow = rows[curIdx + step];
            const prevRow = rows[curIdx - step];
            if (nextRow && (wantsBase ? nextRow.getAttribute("data-old-ln") : nextRow.getAttribute("data-new-ln"))) {
              targetLineEl = nextRow;
              targetLn = wantsBase ? nextRow.getAttribute("data-old-ln") : nextRow.getAttribute("data-new-ln");
              break;
            }
            if (prevRow && (wantsBase ? prevRow.getAttribute("data-old-ln") : prevRow.getAttribute("data-new-ln"))) {
              targetLineEl = prevRow;
              targetLn = wantsBase ? prevRow.getAttribute("data-old-ln") : prevRow.getAttribute("data-new-ln");
              break;
            }
          }
        }
      }
    }

    const idx = Number(targetLineEl.getAttribute("data-idx"));
    const lineNum = Number(targetLn || (idx + 1));
    const filePath = targetLineEl.getAttribute("data-file") || (state.activeLine && state.activeLine.filePath) || "";

    state.activeLine = {
      clNum: num,
      filePath,
      lineNum,
      isBase: wantsBase,
      side: wantsBase ? "PARENT" : "REVISION",
    };
    if (typeof window !== "undefined" && window.state) {
      window.state.activeLine = state.activeLine;
    }

    setActivePgLineElement(targetLineEl, wantsBase);
    syncPgDiffLineHash(filePath, lineNum, wantsBase);

    if (typeof targetLineEl.scrollIntoView === "function") {
      targetLineEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    if (typeof showToast === "function") {
      const sideName = wantsBase ? "Base (Left / a)" : "Revision (Right / b)";
      showToast(`Diff side: ${sideName} · Line ${wantsBase ? "b" : ""}${lineNum}`);
    }
    return true;
  }

  function onDiffLineGutterClick(event, lnEl) {
    if (state.suppressGutterClick) {
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
      return;
    }
    if (event && typeof event.stopPropagation === "function") event.stopPropagation();
    const lineEl = lnEl ? (lnEl.closest(".code-line[data-idx]") || lnEl.closest(".code-line")) : null;
    if (!lineEl) return;
    const tableEl = lineEl.closest(".code-table");
    if (!tableEl) return;
    const directIdx = lnEl ? lnEl.getAttribute("data-idx") : null;
    const clickedIdx = (directIdx != null && directIdx !== "") ? Number(directIdx) : Number(lineEl.getAttribute("data-idx"));
    if (Number.isNaN(clickedIdx)) return;

    const prev = state.codeSelection;
    const sideAttr = lnEl ? lnEl.getAttribute("data-side") : "";
    const preferredSide = sideAttr === "left" ? "PARENT" : (sideAttr === "right" ? "REVISION" : null);
    const sameFile = Boolean(
      prev &&
      prev.filePath === (lineEl.getAttribute("data-file") || "") &&
      Number(prev.clNum) === Number(lineEl.getAttribute("data-cl"))
    );
    const isAsk = Boolean(event && event.altKey);
    const isAddBtn = Boolean(event && event.target && event.target.classList && event.target.classList.contains("ln-add-btn"));

    const isOld = preferredSide === "PARENT" || (lnEl && lnEl.classList && lnEl.classList.contains("ln-old"));
    const curLineNum = isOld
      ? (lineEl.getAttribute("data-old-ln") || lineEl.getAttribute("data-new-ln") || (clickedIdx + 1))
      : (lineEl.getAttribute("data-new-ln") || lineEl.getAttribute("data-old-ln") || (clickedIdx + 1));
    const targetFilePath = lineEl.getAttribute("data-file") || (tableEl && tableEl.getAttribute("data-file")) || "";
    const targetClNum = Number(lineEl.getAttribute("data-cl") || (tableEl && tableEl.getAttribute("data-cl")) || state.selectedClNumber || 0);

    if (curLineNum) {
      state.activeLine = {
        clNum: targetClNum,
        filePath: targetFilePath,
        lineNum: Number(curLineNum),
        endLineNum: Number(curLineNum),
        isBase: isOld,
        side: preferredSide || (isOld ? "PARENT" : "REVISION"),
      };
      setActivePgLineElement(lineEl, isOld);
      syncPgDiffLineHash(targetFilePath, curLineNum, isOld);
    }

    if (event && event.shiftKey && sameFile && prev.anchorIdx != null) {
      selectCodeBlockRange(tableEl, prev.anchorIdx, clickedIdx, {
        anchorIdx: prev.anchorIdx,
        focusInput: true,
        mode: isAsk ? "ask" : "comment",
        side: preferredSide,
      });
      const sLn = (state.codeSelection && state.codeSelection.startLine)
        ? state.codeSelection.startLine
        : Math.min(prev.anchorIdx, clickedIdx) + 1;
      const eLn = (state.codeSelection && state.codeSelection.endLine)
        ? state.codeSelection.endLine
        : Math.max(prev.anchorIdx, clickedIdx) + 1;
      const isRange = eLn > sLn;
      syncPgDiffLineHash(targetFilePath, sLn, isOld, isRange ? eLn : null);
      if (typeof showToast === "function") {
        showToast(`Selected line range L${sLn}–L${eLn} (Shortcut: c to comment · Alt+2 to Ask Agent · y to copy link)`);
      }
    } else {
      const isAlreadyOpen = sameFile && prev && prev.startIdx === clickedIdx && prev.endIdx === clickedIdx && (!preferredSide || prev.side === preferredSide);
      if (isAlreadyOpen) {
        if (isAsk && prev.mode !== "ask") {
          if (typeof setCodeSelectionMode === "function") setCodeSelectionMode("ask");
        } else if (!isAsk && prev.mode !== "comment" && isAddBtn) {
          if (typeof setCodeSelectionMode === "function") setCodeSelectionMode("comment");
        }
        const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
        if (inputEl && typeof inputEl.focus === "function") {
          inputEl.focus();
          try {
            const valLen = inputEl.value.length;
            inputEl.setSelectionRange(valLen, valLen);
          } catch (_) {}
        }
        const ln = preferredSide === "PARENT"
          ? (lineEl.getAttribute("data-old-ln") || lineEl.getAttribute("data-new-ln") || (clickedIdx + 1))
          : (lineEl.getAttribute("data-new-ln") || lineEl.getAttribute("data-old-ln") || (clickedIdx + 1));
        const sideNotice = preferredSide === "PARENT" ? " (Base / Parent)" : "";
        if (typeof showToast === "function") {
          showToast(`Composer focused on line ${ln}${sideNotice} (Ctrl+Enter to save draft)`);
        }
      } else {
        selectCodeBlockRange(tableEl, clickedIdx, clickedIdx, {
          anchorIdx: clickedIdx,
          focusInput: true,
          mode: isAsk ? "ask" : "comment",
          side: preferredSide,
        });
        const ln = preferredSide === "PARENT"
          ? (lineEl.getAttribute("data-old-ln") || lineEl.getAttribute("data-new-ln") || (clickedIdx + 1))
          : (lineEl.getAttribute("data-new-ln") || lineEl.getAttribute("data-old-ln") || (clickedIdx + 1));
        const sideNotice = preferredSide === "PARENT" ? " (Base / Parent)" : "";
        if (typeof showToast === "function") {
          if (isAsk) {
            showToast(`🤖 Opened Ask Agent on line ${ln}${sideNotice} (Shortcut: Alt+2 · Enter prompt)`);
          } else {
            showToast(`Opened comment draft on line ${ln}${sideNotice} (Shortcut: c · Ctrl+Enter to save)`);
          }
        }
      }
    }
  }

  function onDiffLineGutterContextMenu(event, lnEl) {
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    if (event && typeof event.stopPropagation === "function") {
      event.stopPropagation();
    }
    const lineEl = lnEl ? (lnEl.closest(".code-line[data-idx]") || lnEl.closest(".code-line")) : null;
    if (!lineEl) return;
    const tableEl = lineEl.closest(".code-table");
    const clNum = Number(lineEl.getAttribute("data-cl") || (tableEl && tableEl.getAttribute("data-cl")) || state.selectedClNumber || 0);
    const filePath = lineEl.getAttribute("data-file") || (tableEl && tableEl.getAttribute("data-file")) || "";
    const sideAttr = lnEl ? lnEl.getAttribute("data-side") : "";
    const isBase = sideAttr === "left" || (lnEl && lnEl.classList && lnEl.classList.contains("ln-old"));
    const directIdx = lnEl ? lnEl.getAttribute("data-idx") : null;
    const clickedIdx = (directIdx != null && directIdx !== "") ? Number(directIdx) : Number(lineEl.getAttribute("data-idx"));
    const lineNum = isBase
      ? (lineEl.getAttribute("data-old-ln") || lineEl.getAttribute("data-new-ln") || (clickedIdx + 1))
      : (lineEl.getAttribute("data-new-ln") || lineEl.getAttribute("data-old-ln") || (clickedIdx + 1));
    if (!lineNum) return;

    setActivePgLineElement(lineEl, isBase);
    const sel = state.codeSelection;
    if (sel && sel.filePath === filePath && sel.startLine && sel.endLine && sel.endLine > sel.startLine &&
        Number(lineNum) >= sel.startLine && Number(lineNum) <= sel.endLine) {
      copyPgDiffLinePermalink(clNum, filePath, sel.startLine, isBase, sel.endLine);
    } else {
      copyPgDiffLinePermalink(clNum, filePath, lineNum, isBase);
    }
  }

  function triggerPgLineComment(clNum, filePath, lineIdx, opts = {}) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return false;
    const safeFileId = String(filePath).replace(/[^a-zA-Z0-9_-]/g, "_");
    const block = document.getElementById(`file-diff-${num}-${safeFileId}`) ||
                  document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${filePath}"]`);
    if (!block) return false;
    if (block.classList && block.classList.contains("is-collapsed")) {
      block.classList.remove("is-collapsed");
      const ps = Number(block.getAttribute("data-ps") || 1);
      if (typeof setPgFileCollapsed === "function") {
        setPgFileCollapsed(num, filePath, false, ps);
      }
    }
    const tableEl = block.querySelector(".code-table");
    if (!tableEl) return false;
    const idx = Number(lineIdx);
    if (Number.isNaN(idx)) return false;
    const side = opts.side || "REVISION";
    const mode = opts.mode || (opts.ask ? "ask" : "comment");
    selectCodeBlockRange(tableEl, idx, idx, {
      anchorIdx: idx,
      focusInput: true,
      mode,
      side,
    });
    return true;
  }

  function triggerPgAddComment(clNum, targetFilePath = null) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return false;

    // 1. If explicit targetFilePath is provided (e.g. from file header Comment button)
    if (targetFilePath) {
      if (targetFilePath === "/COMMIT_MSG") {
        if (typeof selectFullCommitMessage === "function") {
          selectFullCommitMessage(num, "comment");
          if (typeof showToast === "function") {
            showToast("Add comment on /COMMIT_MSG (Shortcut: c) · Ctrl+Enter to save draft");
          }
          return true;
        }
      }
      const safeFileId = String(targetFilePath).replace(/[^a-zA-Z0-9_-]/g, "_");
      let block = document.getElementById(`file-diff-${num}-${safeFileId}`) ||
                  document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${targetFilePath}"]`);
      if (block) {
        if (block.classList && block.classList.contains("is-collapsed")) {
          block.classList.remove("is-collapsed");
          const ps = Number(block.getAttribute("data-ps") || 1);
          if (typeof setPgFileCollapsed === "function") {
            setPgFileCollapsed(num, targetFilePath, false, ps);
          }
        }
        const tableEl = block.querySelector(".code-table");
        if (tableEl) {
          const targetLine = tableEl.querySelector(".code-line[data-type=\"add\"]") ||
                             tableEl.querySelector(".code-line[data-type=\"del\"]") ||
                             tableEl.querySelector(".code-line[data-idx]");
          if (targetLine) {
            const idx = Number(targetLine.getAttribute("data-idx"));
            if (!Number.isNaN(idx)) {
              selectCodeBlockRange(tableEl, idx, idx, { anchorIdx: idx, focusInput: true, mode: "comment" });
              const baseName = String(targetFilePath).split("/").pop() || targetFilePath;
              const ln = targetLine.getAttribute("data-new-ln") || targetLine.getAttribute("data-old-ln") || (idx + 1);
              if (typeof showToast === "function") {
                showToast(`Add comment on ${baseName} (L${ln}) · Press Ctrl+Enter to save draft`);
              }
              return true;
            }
          }
        }
      }
    }

    // 2. If code selection composer is already mounted and active or range selected
    if (state.codeSelection) {
      if (typeof setCodeSelectionMode === "function") {
        setCodeSelectionMode("comment");
      }
      let inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
      if (!inputEl) {
        const tableEl = state.codeSelection.tableEl ||
          document.querySelector(`.pg-file-diff-block[data-file="${state.codeSelection.filePath}"] .code-table`) ||
          document.querySelector(".code-table");
        if (tableEl) {
          const selectedEls = Array.from(tableEl.querySelectorAll(".code-line.code-line-selected"));
          const lastEl = selectedEls.length > 0 ? selectedEls[selectedEls.length - 1] : tableEl.querySelector(`.code-line[data-idx="${state.codeSelection.endIdx}"]`);
          if (lastEl) {
            mountCodeSelectionComposer(lastEl, true);
            inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
          }
        }
      }
      if (inputEl) {
        inputEl.focus();
        try {
          const valLen = inputEl.value.length;
          inputEl.setSelectionRange(valLen, valLen);
        } catch (_) {}
        if (typeof showToast === "function") {
          showToast("Comment composer focused (Shortcut: c · Ctrl+Enter to save draft)");
        }
        return true;
      }
    }

    // 3. If an in-place draft editor or inline reply composer is currently open in DOM
    const activeTextarea = document.querySelector(".pg-inline-draft-textarea, .inline-reply-editor textarea");
    if (activeTextarea) {
      activeTextarea.focus();
      if (typeof showToast === "function") {
        showToast("Inline comment editor focused (Ctrl+Enter to save)");
      }
      return true;
    }

    // 4. If an existing published or draft comment thread is hovered or focused
    const activeThread = document.querySelector(".diff-inline-comment:not(.draft-item):hover, .diff-inline-comment:not(.draft-item):focus-within");
    if (activeThread) {
      const threadId = activeThread.getAttribute("data-inline-id");
      if (threadId && typeof openInlineReplyComposer === "function") {
        openInlineReplyComposer(num, threadId);
        if (typeof showToast === "function") {
          showToast("Opened reply composer on comment thread (Shortcut: c / r)");
        }
        return true;
      }
    }

    // 5. If user has text highlighted via browser selection in diff
    const sel = (typeof window !== "undefined" && window.getSelection) ? window.getSelection() : null;
    if (sel && !sel.isCollapsed && sel.toString().trim()) {
      const findCodeLine = (node) => {
        if (!node) return null;
        const el = node.nodeType === 1 ? node : node.parentElement;
        return el ? el.closest(".code-line[data-idx]") : null;
      };
      const anchorLine = findCodeLine(sel.anchorNode);
      const focusLine = findCodeLine(sel.focusNode);
      if (anchorLine || focusLine) {
        const startLineEl = anchorLine || focusLine;
        const endLineEl = focusLine || anchorLine;
        const tableA = startLineEl.closest(".code-table");
        const tableB = endLineEl.closest(".code-table");
        if (tableA && tableA === tableB) {
          const idxA = Number(startLineEl.getAttribute("data-idx"));
          const idxB = Number(endLineEl.getAttribute("data-idx"));
          if (!Number.isNaN(idxA) && !Number.isNaN(idxB)) {
            selectCodeBlockRange(tableA, idxA, idxB, { anchorIdx: idxA, focusInput: true, mode: "comment" });
            const sLn = startLineEl.getAttribute("data-new-ln") || startLineEl.getAttribute("data-old-ln") || idxA;
            const eLn = endLineEl.getAttribute("data-new-ln") || endLineEl.getAttribute("data-old-ln") || idxB;
            const rangeStr = (sLn === eLn) ? `L${sLn}` : `L${Math.min(Number(sLn), Number(eLn))}-L${Math.max(Number(sLn), Number(eLn))}`;
            if (typeof showToast === "function") {
              showToast(`Opened comment composer on ${rangeStr} (Shortcut: c)`);
            }
            return true;
          }
        }
      }
    }

    // 6. If a code line in the diff is hovered, focused, or activeLine
    const activeLineEl = document.querySelector(".code-line[data-idx]:hover, .code-line[data-idx]:focus-within") ||
      (document.activeElement && document.activeElement.closest ? document.activeElement.closest(".code-line[data-idx]") : null) ||
      document.querySelector(".code-line.pg-line-focused[data-idx]") ||
      (state.activeLine && state.activeLine.filePath ? document.querySelector(`.code-line[data-file="${state.activeLine.filePath}"][data-new-ln="${state.activeLine.lineNum}"], .code-line[data-file="${state.activeLine.filePath}"][data-old-ln="${state.activeLine.lineNum}"]`) : null);
    if (activeLineEl) {
      const tableEl = activeLineEl.closest(".code-table");
      const idx = Number(activeLineEl.getAttribute("data-idx"));
      if (tableEl && !Number.isNaN(idx)) {
        const isOld = (state.activeLine && state.activeLine.filePath === activeLineEl.getAttribute("data-file")) ? Boolean(state.activeLine.isBase) : false;
        selectCodeBlockRange(tableEl, idx, idx, { anchorIdx: idx, focusInput: true, mode: "comment", side: isOld ? "PARENT" : "REVISION" });
        const ln = isOld
          ? (activeLineEl.getAttribute("data-old-ln") || activeLineEl.getAttribute("data-new-ln") || (idx + 1))
          : (activeLineEl.getAttribute("data-new-ln") || activeLineEl.getAttribute("data-old-ln") || (idx + 1));
        const sideNotice = isOld ? " (Base / Parent)" : "";
        if (typeof showToast === "function") {
          showToast(`Opened comment composer on line ${isOld ? "b" : ""}${ln}${sideNotice} (Shortcut: c · Ctrl+Enter to save draft)`);
        }
        return true;
      }
    }

    // 7. Check active or visible file diff block
    const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
    const targetBlock = activeBlock ||
      document.querySelector(`.pg-file-diff-block[data-cl="${num}"]:not(.is-collapsed)`) ||
      document.querySelector(`.pg-file-diff-block:not(.is-collapsed)`);
    if (targetBlock) {
      const tableEl = targetBlock.querySelector(".code-table");
      if (tableEl) {
        const changedLine = tableEl.querySelector(".code-line[data-type=\"add\"]") ||
                            tableEl.querySelector(".code-line[data-type=\"del\"]") ||
                            tableEl.querySelector(".code-line[data-idx]");
        if (changedLine) {
          const idx = Number(changedLine.getAttribute("data-idx"));
          if (!Number.isNaN(idx)) {
            selectCodeBlockRange(tableEl, idx, idx, { anchorIdx: idx, focusInput: true, mode: "comment" });
            const filePath = targetBlock.getAttribute("data-file") || "";
            const baseName = filePath.split("/").pop() || filePath;
            const ln = changedLine.getAttribute("data-new-ln") || changedLine.getAttribute("data-old-ln") || (idx + 1);
            if (typeof showToast === "function") {
              showToast(`Add comment on ${baseName} (L${ln}) · Press Ctrl+Enter to save draft`);
            }
            return true;
          }
        }
      }
    }

    // 8. Check if /COMMIT_MSG is visible
    const commitMsgPanel = document.getElementById(`commitMsgPanel-${num}`) || document.querySelector(".commit-msg-panel");
    if (commitMsgPanel) {
      const firstLine = commitMsgPanel.querySelector(".commit-msg-line[data-idx]");
      const tableEl = firstLine ? firstLine.closest(".code-table") : null;
      if (tableEl && firstLine) {
        const idx = Number(firstLine.getAttribute("data-idx"));
        if (!Number.isNaN(idx)) {
          selectCodeBlockRange(tableEl, idx, idx, { anchorIdx: idx, focusInput: true, mode: "comment" });
          if (typeof showToast === "function") {
            showToast("Add comment on /COMMIT_MSG · Press Ctrl+Enter to save draft");
          }
          return true;
        }
      }
    }

    // 9. If no file diff is uncollapsed yet, expand first file block and select line
    const firstFileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"]`) || document.querySelector(".pg-file-diff-block");
    if (firstFileBlock) {
      const filePath = firstFileBlock.getAttribute("data-file");
      if (filePath && typeof setPgFileCollapsed === "function") {
        const ps = Number(firstFileBlock.getAttribute("data-ps") || 1);
        setPgFileCollapsed(num, filePath, false, ps);
        const tableEl = firstFileBlock.querySelector(".code-table");
        const lineEl = tableEl ? (tableEl.querySelector(".code-line[data-type=\"add\"]") || tableEl.querySelector(".code-line[data-type=\"del\"]") || tableEl.querySelector(".code-line[data-idx]")) : null;
        if (tableEl && lineEl) {
          const idx = Number(lineEl.getAttribute("data-idx"));
          if (!Number.isNaN(idx)) {
            selectCodeBlockRange(tableEl, idx, idx, { anchorIdx: idx, focusInput: true, mode: "comment" });
            const baseName = filePath.split("/").pop() || filePath;
            const ln = lineEl.getAttribute("data-new-ln") || lineEl.getAttribute("data-old-ln") || (idx + 1);
            if (typeof showToast === "function") {
              showToast(`Expanded ${baseName} and opened comment composer (L${ln})`);
            }
            return true;
          }
        }
      }
    }

    if (typeof showToast === "function") {
      showToast("Expand a file diff or select code lines to add a comment (Shortcut: c)");
    }
    return false;
  }

  function setCodeSelectionMode(mode, opts = {}) {
    if (!state.codeSelection) return;
    const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
    if (inputEl) {
      const curVal = inputEl.value;
      state.codeSelection.draft = curVal;
      if (state.codeSelection.mode === "ask") {
        state.codeSelection.askDraft = curVal;
      } else {
        state.codeSelection.commentDraft = curVal;
      }
    }
    const resolved = mode === "ask" ? "ask" : "comment";
    const modeChanged = state.codeSelection.mode !== resolved;
    state.codeSelection.mode = resolved;
    const composerEl = document.getElementById("codeSelectionComposer");
    if (composerEl) {
      composerEl.innerHTML = buildCodeSelectionComposerInnerHtml(state.codeSelection);
    }
    if (opts && opts.focusTab) {
      const tabEl = document.getElementById(resolved === "ask" ? "btnComposerTabAsk" : "btnComposerTabComment");
      if (tabEl && typeof tabEl.focus === "function") {
        tabEl.focus();
      }
    } else {
      const newInputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
      if (newInputEl && typeof newInputEl.focus === "function") {
        newInputEl.focus();
        try {
          const len = newInputEl.value.length;
          newInputEl.setSelectionRange(len, len);
        } catch (_) {}
      }
    }
    if (opts && (opts.showToast || (modeChanged && opts.showToast !== false && opts.fromKeyboard))) {
      if (typeof showToast === "function") {
        const modeLabel = resolved === "ask" ? "Ask Agent mode (Alt+2)" : "Review Comment mode (Alt+1)";
        showToast(`Switched code composer to ${modeLabel}`);
      }
    }
  }

  function onComposerModeTabKeydown(event, tabMode) {
    if (!event) return;
    const key = event.key;
    if (key === "ArrowRight" || key === "ArrowDown") {
      event.preventDefault();
      setCodeSelectionMode("ask", { focusTab: true, fromKeyboard: true });
      return;
    }
    if (key === "ArrowLeft" || key === "ArrowUp") {
      event.preventDefault();
      setCodeSelectionMode("comment", { focusTab: true, fromKeyboard: true });
      return;
    }
    if (key === "Home") {
      event.preventDefault();
      setCodeSelectionMode("comment", { focusTab: true, fromKeyboard: true });
      return;
    }
    if (key === "End") {
      event.preventDefault();
      setCodeSelectionMode("ask", { focusTab: true, fromKeyboard: true });
      return;
    }
    if (key === "Enter" || key === " ") {
      event.preventDefault();
      setCodeSelectionMode(tabMode, { focusTextarea: true, fromKeyboard: true });
      return;
    }
    if (key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
      if (inputEl && typeof inputEl.focus === "function") {
        inputEl.focus();
        try {
          const len = inputEl.value.length;
          inputEl.setSelectionRange(len, len);
        } catch (_) {}
      }
      return;
    }
    if (key === "Escape") {
      event.preventDefault();
      closeCodeSelectionComposer(event, { discardDraft: true });
      return;
    }
  }

  function applyQuickReviewComment(text) {
    if (!state.codeSelection) return;
    const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
    if (inputEl) {
      const cur = inputEl.value.trim();
      inputEl.value = cur ? `${cur}\n${text}` : text;
      state.codeSelection.draft = inputEl.value;
      state.codeSelection.commentDraft = inputEl.value;
      inputEl.focus();
      try {
        const len = inputEl.value.length;
        inputEl.setSelectionRange(len, len);
      } catch (_) {}
    }
  }

  function updatePgComposerAutosaveBadge(status = "saved") {
    const badge = document.getElementById("composerAutosaveBadge");
    if (!badge) return;
    badge.className = `pg-draft-autosave-badge is-${status}`;
    if (badge.classList && typeof badge.classList.add === "function") {
      badge.classList.remove("is-saved", "is-unsaved", "is-saving");
      badge.classList.add("pg-draft-autosave-badge", `is-${status}`);
    }
    if (status === "unsaved") {
      badge.textContent = "● Unsaved";
      badge.title = "Unsaved draft edits (autosaves after pause or on blur)";
    } else if (status === "saving") {
      badge.textContent = "Saving…";
      badge.title = "Autosaving draft comment…";
    } else {
      badge.textContent = "✓ Saved";
      badge.title = `Draft changes automatically saved at ${new Date().toLocaleTimeString()}`;
    }
  }

  function schedulePgComposerDraftAutosave() {
    if (state.composerAutosaveTimer) {
      clearTimeout(state.composerAutosaveTimer);
    }
    state.composerAutosaveTimer = setTimeout(() => {
      flushPgComposerDraftAutosave();
    }, 750);
  }

  function flushPgComposerDraftAutosave() {
    if (state.composerAutosaveTimer) {
      clearTimeout(state.composerAutosaveTimer);
      state.composerAutosaveTimer = null;
    }
    if (!state.codeSelection) return;
    state.codeSelection.saveStatus = "saved";
    updatePgComposerAutosaveBadge("saved");
  }

  function onCodeComposerInput(_mode, val) {
    if (!state.codeSelection) return;
    state.codeSelection.draft = val;
    state.codeSelection.saveStatus = "unsaved";
    if (state.codeSelection.mode === "ask") {
      state.codeSelection.askDraft = val;
    } else {
      state.codeSelection.commentDraft = val;
    }
    updatePgComposerAutosaveBadge("unsaved");
    schedulePgComposerDraftAutosave();
  }

  function onCodeComposerKeydown(event, mode) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeCodeSelectionComposer(event, { discardDraft: true });
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (mode === "ask") {
        submitSelectedCodeQuestion(document.getElementById("btnSubmitCodeAsk"));
      } else if (event.shiftKey) {
        submitSelectedCodeComment(document.getElementById("btnSubmitCodeComment"));
      } else {
        saveSelectionDraftFromComposer();
      }
      return;
    }
    if (event.altKey && (event.key === "1" || event.code === "Digit1")) {
      event.preventDefault();
      setCodeSelectionMode("comment", { fromKeyboard: true });
      return;
    }
    if (event.altKey && (event.key === "2" || event.code === "Digit2")) {
      event.preventDefault();
      setCodeSelectionMode("ask", { fromKeyboard: true });
      return;
    }
    if (event.altKey && (event.key === "m" || event.key === "M" || event.code === "KeyM")) {
      event.preventDefault();
      const nextMode = mode === "comment" ? "ask" : "comment";
      setCodeSelectionMode(nextMode, { fromKeyboard: true });
      return;
    }
    if (event.key === "Tab") {
      const target = event.target;
      const val = target ? target.value : "";
      const pos = target ? target.selectionStart : 0;
      const endPos = target ? target.selectionEnd : 0;
      if (event.shiftKey) {
        if (pos === 0 && endPos === 0) {
          event.preventDefault();
          const tabBtn = document.getElementById(mode === "ask" ? "btnComposerTabAsk" : "btnComposerTabComment");
          if (tabBtn && typeof tabBtn.focus === "function") {
            tabBtn.focus();
          }
          return;
        }
      } else if (!event.ctrlKey && !event.metaKey) {
        if (!val || pos === 0 || event.altKey) {
          event.preventDefault();
          const nextMode = mode === "comment" ? "ask" : "comment";
          setCodeSelectionMode(nextMode, { fromKeyboard: true });
          return;
        }
      }
    }
  }

  function applyQuickCodeQuestion(promptText) {
    if (!state.codeSelection) return;
    state.codeSelection.mode = "ask";
    state.codeSelection.draft = promptText;
    state.codeSelection.commentDraft = promptText;
    state.codeSelection.askDraft = promptText;
    const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
    if (inputEl) {
      inputEl.value = promptText;
      inputEl.focus();
    }
  }

  function copySelectedCodeSnippet() {
    const sel = state.codeSelection || (typeof window !== "undefined" && window.state && window.state.codeSelection);
    if (!sel) return;
    navigator.clipboard?.writeText(sel.codeSnippet || "");
    showToast(`Copied ${sel.lineCount} highlighted code line(s)!`);
  }

  function copySelectedCodePermalink() {
    const sel = state.codeSelection || (typeof window !== "undefined" && window.state && window.state.codeSelection);
    if (!sel || !sel.filePath) {
      return copyPgDiffActiveLinePermalink();
    }
    const isBase = sel.side === "PARENT";
    const startLine = (sel.startLine != null && Number(sel.startLine) > 0)
      ? Number(sel.startLine)
      : (sel.startIdx != null ? sel.startIdx + 1 : 1);
    const endLine = (sel.endLine != null && Number(sel.endLine) >= startLine)
      ? Number(sel.endLine)
      : startLine;
    return copyPgDiffLinePermalink(sel.clNum, sel.filePath, startLine, isBase, endLine > startLine ? endLine : null);
  }

  function copySelectedCodeMarkdown() {
    const sel = state.codeSelection || (typeof window !== "undefined" && window.state && window.state.codeSelection);
    if (!sel || !sel.filePath) {
      if (typeof showToast === "function") showToast("Select a code block first to copy as Markdown");
      return null;
    }
    const num = Number(sel.clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const isBase = sel.side === "PARENT";
    const startLine = (sel.startLine != null && Number(sel.startLine) > 0)
      ? Number(sel.startLine)
      : (sel.startIdx != null ? sel.startIdx + 1 : 1);
    const endLine = (sel.endLine != null && Number(sel.endLine) >= startLine)
      ? Number(sel.endLine)
      : startLine;
    const isRange = endLine > startLine;
    const permalink = buildPgDiffLinePermalink(num, sel.filePath, startLine, isBase, isRange ? endLine : null);

    const ext = (sel.filePath.split(".").pop() || "").toLowerCase();
    const langMap = {
      cc: "cpp", cpp: "cpp", cxx: "cpp", c: "c", h: "cpp", hpp: "cpp",
      py: "python", js: "javascript", ts: "typescript", json: "json",
      md: "markdown", sh: "bash", bzl: "python", proto: "protobuf",
      rs: "rust", go: "go", java: "java", xml: "xml", html: "html"
    };
    const langFence = langMap[ext] || "";

    const rawSnippet = sel.codeSnippet || "";
    const cleanLines = rawSnippet.split(/\r?\n/).map(line => {
      if (line.startsWith("+") || line.startsWith("-") || line.startsWith(" ")) {
        return line.slice(1);
      }
      return line;
    }).join("\n");

    const rangeStr = isRange ? `L${startLine}–L${endLine}` : `L${startLine}`;
    const sideSuffix = isBase ? " (Base)" : "";
    const markdown = `[${sel.filePath}#${rangeStr}${sideSuffix}](${permalink})\n\`\`\`${langFence}\n${cleanLines}\n\`\`\``;

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(markdown).then(() => {
        if (typeof showToast === "function") {
          showToast(`📋 Copied Markdown quote for ${sel.filePath}:${rangeStr}`);
        }
      }).catch(() => {
        if (typeof showToast === "function") {
          showToast("Failed to copy Markdown to clipboard");
        }
      });
    } else if (typeof showToast === "function") {
      showToast(`Markdown snippet prepared for ${sel.filePath}:${rangeStr}`);
    }
    return markdown;
  }

  function copyLastSentCodePrompt() {
    if (!state.codeSelection || !state.codeSelection.sentPrompt) return;
    navigator.clipboard?.writeText(state.codeSelection.sentPrompt);
    showToast("Copied agent question prompt to clipboard!");
  }

  async function submitSelectedCodeComment(btnEl) {
    const sel = state.codeSelection;
    if (!sel) return;
    const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
    const message = (inputEl ? inputEl.value : (sel.draft || sel.commentDraft || sel.askDraft || "")).trim();
    if (!message) {
      showToast("Please enter a comment message before posting.");
      if (inputEl) inputEl.focus();
      return;
    }
    const chkEl = document.getElementById("codeBlockUnresolvedChk");
    const unresolved = chkEl ? Boolean(chkEl.checked) : Boolean(sel.unresolved);

    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = `<span class="btn-spinner"></span> Posting to Gerrit…`;
    }
    try {
      const resp = await sidecarFetch("/api/gerrit/comment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: sel.clNum,
          file_path: sel.filePath,
          line: sel.endLine,
          start_line: sel.startLine,
          end_line: sel.endLine,
          side: sel.side,
          unresolved,
          message,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(data.error || "Failed to post inline comment to Gerrit");
      }

      if (!state.inlineCommentsByCl[sel.clNum]) {
        state.inlineCommentsByCl[sel.clNum] = [];
      }
      const authorName = data.author_name || (state.viewer || "you").split("@")[0];
      const postedCommentId = String(data.comment_id || "");
      const postedPatchSet = Number(data.patch_set || 0) || null;
      const commentItem = {
        id: `inline-${state.nextInlineItemId++}`,
        kind: "comment",
        comment_id: postedCommentId,
        patch_set: postedPatchSet,
        author_name: authorName,
        root_author_name: authorName,
        file_path: sel.filePath,
        line: sel.endLine,
        start_line: sel.startLine,
        end_line: sel.endLine,
        start_idx: sel.startIdx,
        end_idx: sel.endIdx,
        side: sel.side,
        alt_id: sel.altId || "",
        alt_title: sel.altTitle || "",
        unresolved,
        message,
        messages: [
          {
            comment_id: postedCommentId,
            author_name: authorName,
            patch_set: postedPatchSet,
            message,
            unresolved,
          },
        ],
        collapsed: false,
      };
      sel.savedInlineId = commentItem.id;
      state.inlineCommentsByCl[sel.clNum].push(commentItem);

      const rangeLabel = sel.startLine === sel.endLine
        ? `L${sel.startLine}`
        : `L${sel.startLine}–L${sel.endLine}`;
      clearCodeSelection();
      invalidateClientQueueCache();
      insertOrReplaceInlineItemInDom(sel.clNum, commentItem);
      document.getElementById("drawerStatus").textContent =
        `Posted inline comment on ag/${sel.clNum} (${sel.filePath}:${rangeLabel}, notify: OWNER)`;
      showToast(`✓ Posted comment on ${sel.filePath}:${rangeLabel} (notify: OWNER)!`);
    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = "💬 Post Comment to Gerrit (notify: OWNER)";
      }
      showToast("Comment post failed: " + err);
    }
  }

  function openInlineReplyComposer(clNum, itemId) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    const item = findInlineItemById(num, idStr);
    if (!item) return;
    if (idStr.startsWith("srv-thread-")) {
      state.collapsedInlineIds.delete(idStr);
    }
    item.collapsed = false;
    if (!state.openReplyByItemId[idStr]) {
      state.openReplyByItemId[idStr] = {
        draft: "",
        unresolved: item.unresolved !== false,
        submitting: false,
        error: "",
        isSavedDraft: false,
      };
    } else {
      state.openReplyByItemId[idStr].isSavedDraft = false;
    }
    insertOrReplaceInlineItemInDom(num, item, {
      forceRender: true,
      focusTextareaId: `commentReplyInput-${idStr}`,
    });
  }

  function cancelInlineReplyComposer(clNum, itemId, skipConfirm = false) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    if (state.replyAutosaveTimers && state.replyAutosaveTimers[idStr]) {
      clearTimeout(state.replyAutosaveTimers[idStr]);
      delete state.replyAutosaveTimers[idStr];
    }
    const replyState = state.openReplyByItemId[idStr];
    if (replyState && replyState.submitting) return;
    const text = String((replyState && replyState.draft) || "").trim();
    if (!skipConfirm && text) {
      openPgConfirmDiscardDialog({
        title: "Discard Reply Draft",
        subtitle: `ag/${num} · Unsent reply`,
        message: "You have unsaved text in this reply composer. Are you sure you want to discard it?",
        previewText: text,
        confirmLabel: "Discard Reply",
        onConfirm: () => {
          cancelInlineReplyComposer(num, idStr, true);
        }
      });
      return;
    }
    delete state.openReplyByItemId[idStr];
    const item = findInlineItemById(num, idStr);
    if (item) {
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
      const cardEl = document.getElementById(`inlineItem-${idStr}`);
      const replyBtn = cardEl ? cardEl.querySelector(".inline-reply-btn") : null;
      if (replyBtn) replyBtn.focus();
    }
  }

  function updatePgReplyDraftAutosaveBadge(itemId, status = "saved") {
    const idStr = String(itemId || "");
    const badge = document.getElementById(`inlineReplyAutosaveBadge-${idStr}`);
    if (!badge) return;
    badge.className = `pg-draft-autosave-badge is-${status}`;
    if (badge.classList && typeof badge.classList.add === "function") {
      badge.classList.remove("is-saved", "is-unsaved", "is-saving");
      badge.classList.add("pg-draft-autosave-badge", `is-${status}`);
    }
    if (status === "unsaved") {
      badge.textContent = "● Unsaved";
      badge.title = "Unsaved reply draft edits (autosaves after pause or on blur)";
    } else if (status === "saving") {
      badge.textContent = "Saving…";
      badge.title = "Autosaving reply draft…";
    } else {
      badge.textContent = "✓ Saved";
      badge.title = `Reply draft automatically saved at ${new Date().toLocaleTimeString()}`;
    }
  }

  function schedulePgReplyDraftAutosave(clNum, itemId) {
    const idStr = String(itemId || "");
    if (!state.replyAutosaveTimers) state.replyAutosaveTimers = {};
    if (state.replyAutosaveTimers[idStr]) {
      clearTimeout(state.replyAutosaveTimers[idStr]);
    }
    state.replyAutosaveTimers[idStr] = setTimeout(() => {
      flushPgReplyDraftAutosave(clNum, idStr);
    }, 750);
  }

  function flushPgReplyDraftAutosave(clNum, itemId) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    if (state.replyAutosaveTimers && state.replyAutosaveTimers[idStr]) {
      clearTimeout(state.replyAutosaveTimers[idStr]);
      delete state.replyAutosaveTimers[idStr];
    }
    const replyState = state.openReplyByItemId[idStr];
    if (!replyState) return;

    const ta = document.getElementById(`commentReplyInput-${idStr}`);
    const draftText = (ta ? ta.value : (replyState.draft || "")).trim();
    if (draftText) {
      replyState.draft = draftText;
      replyState.isSavedDraft = true;
      replyState.savedAt = Date.now();
      replyState.saveStatus = "saved";
      updatePgReplyDraftAutosaveBadge(idStr, "saved");
      updateReplyButtonDraftsBadge(num);
      syncReplyDraftsSectionDom(num);
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
    }
  }

  function onInlineReplyDraftInput(itemId, val) {
    const idStr = String(itemId || "");
    if (state.openReplyByItemId[idStr]) {
      state.openReplyByItemId[idStr].draft = val;
      state.openReplyByItemId[idStr].saveStatus = "unsaved";
      if (state.openReplyByItemId[idStr].error) {
        state.openReplyByItemId[idStr].error = "";
      }
      updatePgReplyDraftAutosaveBadge(idStr, "unsaved");
      schedulePgReplyDraftAutosave(state.selectedClNumber, idStr);
      updateReplyButtonDraftsBadge(state.selectedClNumber);
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
    }
    const ta = document.getElementById(`commentReplyInput-${idStr}`);
    if (ta) autoResizePgTextarea(ta);
  }

  function onInlineReplyResolvedChange(clNum, itemId, isChecked) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    const replyState = state.openReplyByItemId[idStr];
    if (!replyState) return;
    replyState.unresolved = !isChecked;
    const item = findInlineItemById(num, idStr);
    const wasResolved = Boolean(item && item.unresolved === false);
    const pillEl = document.getElementById(`commentReplyPill-${idStr}`);
    if (pillEl) {
      pillEl.textContent = formatInlineReplyStatusPillText(wasResolved, Boolean(isChecked));
      pillEl.classList.toggle("sent", Boolean(isChecked));
    }
    const submitBtn = document.getElementById(`btnSubmitInlineReply-${idStr}`);
    if (submitBtn && !replyState.submitting) {
      submitBtn.textContent = (isChecked && !wasResolved)
        ? "✓ Send Reply & Resolve"
        : "💬 Send Reply";
    }
  }

  function onInlineReplyKeydown(event, clNum, itemId) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      cancelInlineReplyComposer(clNum, itemId);
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      submitInlineCommentReply(clNum, itemId);
    }
  }

  function autoResizePgTextarea(textareaEl) {
    if (!textareaEl) return;
    textareaEl.style.height = "auto";
    const newH = Math.max(68, textareaEl.scrollHeight || 0);
    textareaEl.style.height = `${newH}px`;
  }

  function editInlineDraftInPlace(clNum, itemId) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    const item = findInlineItemById(num, idStr);
    if (!item) return;

    if (!state.openDraftEditByItemId) state.openDraftEditByItemId = {};
    state.openDraftEditByItemId[idStr] = {
      draft: item.message || "",
      original: item.message || "",
      unresolved: item.unresolved !== false,
      tab: "edit",
      saveStatus: "saved",
    };
    item.collapsed = false;

    insertOrReplaceInlineItemInDom(num, item, {
      forceRender: true,
      focusTextareaId: `inlineDraftTextarea-${idStr}`,
    });

    const ta = document.getElementById(`inlineDraftTextarea-${idStr}`);
    if (ta) {
      autoResizePgTextarea(ta);
      ta.focus();
      const len = ta.value.length;
      if (typeof ta.setSelectionRange === "function") ta.setSelectionRange(len, len);
    }
  }

  function saveInlineDraftInPlace(clNum, itemId) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    if (state.draftAutosaveTimers && state.draftAutosaveTimers[idStr]) {
      clearTimeout(state.draftAutosaveTimers[idStr]);
      delete state.draftAutosaveTimers[idStr];
    }
    const item = findInlineItemById(num, idStr);
    if (!item) return;

    const editState = state.openDraftEditByItemId && state.openDraftEditByItemId[idStr];
    const ta = document.getElementById(`inlineDraftTextarea-${idStr}`);
    const nextMsg = (ta ? ta.value : (editState ? editState.draft : item.message || "")).trim();
    if (!nextMsg) {
      discardInlineDraftWithConfirm(num, idStr, "draft");
      return;
    }

    item.message = nextMsg;
    item.saved_at = Date.now();
    item.is_draft = true;
    if (editState && typeof editState.unresolved === "boolean") {
      item.unresolved = editState.unresolved;
    }
    item.collapsed = false;
    if (state.openDraftEditByItemId) {
      delete state.openDraftEditByItemId[idStr];
    }

    insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
    updateReplyButtonDraftsBadge(num);
    syncReplyDraftsSectionDom(num);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    showToast("Draft comment saved ✓");
  }

  function cancelInlineDraftInPlace(clNum, itemId, skipConfirm = false) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    if (state.draftAutosaveTimers && state.draftAutosaveTimers[idStr]) {
      clearTimeout(state.draftAutosaveTimers[idStr]);
      delete state.draftAutosaveTimers[idStr];
    }
    const item = findInlineItemById(num, idStr);
    const editState = state.openDraftEditByItemId && state.openDraftEditByItemId[idStr];
    const ta = document.getElementById(`inlineDraftTextarea-${idStr}`);
    const currentVal = (ta ? ta.value : (editState ? editState.draft : "")).trim();
    const origVal = String(editState ? editState.original : (item ? item.message || "" : "")).trim();

    if (!skipConfirm && currentVal !== origVal && currentVal !== "") {
      openPgConfirmDiscardDialog({
        title: "Discard Draft Edits",
        subtitle: `ag/${num} · L${item ? (item.line || item.start_line || 1) : 1}`,
        message: "You have unsaved changes to this draft comment. Discard changes and revert to original draft?",
        previewText: currentVal,
        confirmLabel: "Discard Edits",
        onConfirm: () => {
          cancelInlineDraftInPlace(num, idStr, true);
        },
      });
      return;
    }

    if (state.openDraftEditByItemId) {
      delete state.openDraftEditByItemId[idStr];
    }
    if (item) {
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
    }
  }

  function setInlineDraftTab(clNum, itemId, tab) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    const item = findInlineItemById(num, idStr);
    if (!item) return;

    if (!state.openDraftEditByItemId) state.openDraftEditByItemId = {};
    if (!state.openDraftEditByItemId[idStr]) {
      state.openDraftEditByItemId[idStr] = {
        draft: item.message || "",
        original: item.message || "",
        unresolved: item.unresolved !== false,
        tab: "edit",
        saveStatus: "saved",
      };
    }
    const ta = document.getElementById(`inlineDraftTextarea-${idStr}`);
    if (ta) {
      state.openDraftEditByItemId[idStr].draft = ta.value;
    }
    state.openDraftEditByItemId[idStr].tab = tab;

    insertOrReplaceInlineItemInDom(num, item, {
      forceRender: true,
      focusTextareaId: tab === "edit" ? `inlineDraftTextarea-${idStr}` : null,
    });

    if (tab === "edit") {
      const nextTa = document.getElementById(`inlineDraftTextarea-${idStr}`);
      if (nextTa) {
        autoResizePgTextarea(nextTa);
        nextTa.focus();
      }
    }
  }

  function updatePgInlineDraftAutosaveBadge(itemId, status = "saved") {
    const idStr = String(itemId || "");
    const badge = document.getElementById(`inlineDraftAutosaveBadge-${idStr}`);
    if (!badge) return;
    badge.className = `pg-draft-autosave-badge is-${status}`;
    if (badge.classList && typeof badge.classList.add === "function") {
      badge.classList.remove("is-saved", "is-unsaved", "is-saving");
      badge.classList.add("pg-draft-autosave-badge", `is-${status}`);
    }
    if (status === "unsaved") {
      badge.textContent = "● Unsaved";
      badge.title = "Unsaved draft edits (autosaves after pause or on blur)";
    } else if (status === "saving") {
      badge.textContent = "Saving…";
      badge.title = "Autosaving draft comment…";
    } else {
      badge.textContent = "✓ Saved";
      badge.title = `Draft changes automatically saved at ${new Date().toLocaleTimeString()}`;
    }
  }

  function schedulePgInlineDraftAutosave(clNum, itemId) {
    const idStr = String(itemId || "");
    if (!state.draftAutosaveTimers) state.draftAutosaveTimers = {};
    if (state.draftAutosaveTimers[idStr]) {
      clearTimeout(state.draftAutosaveTimers[idStr]);
    }
    state.draftAutosaveTimers[idStr] = setTimeout(() => {
      flushPgInlineDraftAutosave(clNum, idStr);
    }, 750);
  }

  function flushPgInlineDraftAutosave(clNum, itemId) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    if (state.draftAutosaveTimers && state.draftAutosaveTimers[idStr]) {
      clearTimeout(state.draftAutosaveTimers[idStr]);
      delete state.draftAutosaveTimers[idStr];
    }
    const item = findInlineItemById(num, idStr);
    const editState = state.openDraftEditByItemId && state.openDraftEditByItemId[idStr];
    if (!item || !editState) return;

    const ta = document.getElementById(`inlineDraftTextarea-${idStr}`);
    const nextMsg = (ta ? ta.value : (editState.draft !== undefined ? editState.draft : item.message || "")).trim();
    if (nextMsg) {
      item.message = nextMsg;
      item.saved_at = Date.now();
      item.is_draft = true;
      if (typeof editState.unresolved === "boolean") {
        item.unresolved = editState.unresolved;
      }
      editState.saveStatus = "saved";
      updatePgInlineDraftAutosaveBadge(idStr, "saved");
      updateReplyButtonDraftsBadge(num);
      syncReplyDraftsSectionDom(num);
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
    }
  }

  function onInlineDraftEditInput(itemId, val) {
    const idStr = String(itemId || "");
    if (!state.openDraftEditByItemId) state.openDraftEditByItemId = {};
    if (!state.openDraftEditByItemId[idStr]) {
      state.openDraftEditByItemId[idStr] = { draft: val, original: "", unresolved: true, tab: "edit", saveStatus: "unsaved" };
    } else {
      state.openDraftEditByItemId[idStr].draft = val;
      state.openDraftEditByItemId[idStr].saveStatus = "unsaved";
    }
    updatePgInlineDraftAutosaveBadge(idStr, "unsaved");
    schedulePgInlineDraftAutosave(state.selectedClNumber, idStr);
  }

  function onInlineDraftResolvedChange(itemId, isChecked) {
    const idStr = String(itemId || "");
    if (!state.openDraftEditByItemId) state.openDraftEditByItemId = {};
    if (state.openDraftEditByItemId[idStr]) {
      state.openDraftEditByItemId[idStr].unresolved = Boolean(isChecked);
    }
    const pill = document.getElementById(`inlineDraftPill-${idStr}`);
    if (pill) {
      pill.textContent = isChecked ? "⚠️ Unresolved" : "✓ Resolved";
      pill.className = `diff-inline-status-pill ${isChecked ? "has-unresolved" : "resolved"}`;
    }
  }

  function onInlineDraftEditKeydown(event, clNum, itemId) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      cancelInlineDraftInPlace(clNum, itemId);
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      saveInlineDraftInPlace(clNum, itemId);
    }
  }

  function saveSelectionDraftFromComposer() {
    if (state.composerAutosaveTimer) {
      clearTimeout(state.composerAutosaveTimer);
      state.composerAutosaveTimer = null;
    }
    const sel = state.codeSelection;
    if (!sel) return;
    const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
    const msg = (inputEl ? inputEl.value : (sel.draft || sel.commentDraft || "")).trim();
    if (!msg) {
      showToast("Please enter draft comment text before saving.");
      if (inputEl && typeof inputEl.focus === "function") inputEl.focus();
      return;
    }
    const chkEl = document.getElementById("codeBlockUnresolvedChk");
    if (chkEl) sel.unresolved = chkEl.checked;
    sel.draft = msg;
    sel.commentDraft = msg;
    saveSelectionDraftAsCollapsedItem(sel);

    if (sel.savedInlineId) {
      const draftItem = (state.inlineCommentsByCl[sel.clNum] || []).find(x => String(x.id) === String(sel.savedInlineId));
      if (draftItem) {
        draftItem.collapsed = false;
        const draftEl = document.getElementById(`inlineItem-${sel.savedInlineId}`);
        if (draftEl) {
          draftEl.classList.remove("collapsed");
          const toggleBtn = draftEl.querySelector(".inline-collapse-btn");
          if (toggleBtn) toggleBtn.textContent = "Hide ▴";
        }
      }
    }

    clearCodeSelection({ discardDraft: true });

    updateReplyButtonDraftsBadge(sel.clNum);
    syncReplyDraftsSectionDom(sel.clNum);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    showToast(`Saved draft comment (publish via Reply dialog) ✓`);
  }

  async function postInlineDraftToGerrit(clNum, itemId) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    const item = findInlineItemById(num, idStr);
    if (!item) return;

    const editState = state.openDraftEditByItemId && state.openDraftEditByItemId[idStr];
    const ta = document.getElementById(`inlineDraftTextarea-${idStr}`);
    const message = (ta ? ta.value : (editState ? editState.draft : item.message || "")).trim();
    if (!message) {
      showToast("Cannot post an empty comment.");
      return;
    }
    const unresolved = editState && typeof editState.unresolved === "boolean" ? editState.unresolved : (item.unresolved !== false);

    const postBtn = document.getElementById(`btnPostInlineDraft-${idStr}`) || document.querySelector(`#inlineItem-${idStr} .btn-approve`);
    if (postBtn) {
      postBtn.disabled = true;
      postBtn.innerHTML = `<span class="btn-spinner"></span> Posting…`;
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/comment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          file_path: item.file_path,
          line: item.end_line || item.line,
          start_line: item.start_line,
          end_line: item.end_line || item.line,
          side: item.side || "REVISION",
          unresolved,
          message,
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }

      if (state.openDraftEditByItemId) delete state.openDraftEditByItemId[idStr];
      state.inlineCommentsByCl[num] = (state.inlineCommentsByCl[num] || []).filter(
        x => String(x.id) !== idStr
      );
      const draftEl = document.getElementById(`inlineItem-${idStr}`);
      if (draftEl) draftEl.remove();

      updateReplyButtonDraftsBadge(num);
      syncReplyDraftsSectionDom(num);
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
      showToast("Comment posted to Gerrit (notify: OWNER) ✓");

      if (typeof reloadCommentsForChange === "function") {
        reloadCommentsForChange(num);
      }
    } catch (err) {
      if (postBtn) {
        postBtn.disabled = false;
        postBtn.innerHTML = "💬 Post to Gerrit";
      }
      showToast(`Failed to post comment: ${err.message}`);
    }
  }

  function closeOpenInlineReplyComposers() {
    let closedAny = false;
    Object.keys(state.openReplyByItemId || {}).forEach(idStr => {
      const entry = state.openReplyByItemId[idStr];
      if (entry && entry.submitting) return;
      const cardEl = document.getElementById(`inlineItem-${idStr}`);
      const clNum = Number((cardEl && cardEl.getAttribute("data-cl")) || state.selectedClNumber || 0);
      delete state.openReplyByItemId[idStr];
      closedAny = true;
      if (clNum) {
        const item = findInlineItemById(clNum, idStr);
        if (item) insertOrReplaceInlineItemInDom(clNum, item, { forceRender: true });
      }
    });
    return closedAny;
  }

  function applySubmittedReplyToThreadItem(num, item, data, replyText, nowUnresolved) {
    const wasUnresolved = item.unresolved !== false;
    const replyAuthor = data.author_name || (state.viewer || "you").split("@")[0];
    const newCommentId = String(data.comment_id || "");
    const resolvedPs = Number(data.patch_set || item.patch_set || 0) || null;
    const newMsgObj = {
      comment_id: newCommentId,
      author_name: replyAuthor,
      patch_set: resolvedPs,
      message: replyText,
      unresolved: nowUnresolved,
    };

    if (item.raw_thread) {
      const rt = item.raw_thread;
      if (!Array.isArray(rt.messages) || rt.messages.length === 0) {
        rt.messages = [
          {
            comment_id: String(rt.leaf_id || rt.root_id || ""),
            author_name: rt.author_name || "reviewer",
            patch_set: Number(rt.patch_set || 0) || null,
            message: rt.latest_message || item.message || "",
            unresolved: wasUnresolved,
          },
        ];
      }
      rt.messages.push(newMsgObj);
      rt.latest_message = replyText;
      rt.unresolved = nowUnresolved;
      if (newCommentId) rt.leaf_id = newCommentId;
      if (resolvedPs) rt.patch_set = resolvedPs;
      if (wasUnresolved && !nowUnresolved) {
        document.querySelectorAll(`.swarm-finding[data-thread-ui-id="${item.id}"]`).forEach(el => el.remove());
      }
    } else {
      if (!Array.isArray(item.messages) || item.messages.length === 0) {
        item.messages = [
          {
            comment_id: String(item.comment_id || ""),
            author_name: item.root_author_name || item.author_name || "you",
            patch_set: item.patch_set || null,
            message: item.message || "",
            unresolved: wasUnresolved,
          },
        ];
      }
      item.messages.push(newMsgObj);
      item.message = replyText;
      item.author_name = replyAuthor;
      item.unresolved = nowUnresolved;
      if (newCommentId) item.comment_id = newCommentId;
      if (resolvedPs) item.patch_set = resolvedPs;
    }

    const changeObj = findChangeByNumber(num);
    if (changeObj && wasUnresolved !== nowUnresolved) {
      changeObj.unresolved_comment_count = Math.max(
        0,
        Number(changeObj.unresolved_comment_count || 0) + (nowUnresolved ? 1 : -1)
      );
      const rowSlot = document.getElementById(`presubmitRowSlot-${num}`);
      if (rowSlot) {
        rowSlot.innerHTML = renderCommitPresubmitRowHtml(changeObj);
      }
      const btnToggle = document.getElementById(`btnToggleResolvedComments-${num}`);
      if (btnToggle) {
        const curCount = Number(btnToggle.getAttribute("data-resolved-count") || 0);
        const nextCount = Math.max(0, curCount + (nowUnresolved ? -1 : 1));
        btnToggle.setAttribute("data-resolved-count", String(nextCount));
        const isShown = isPgShowResolvedCommentsEnabled();
        btnToggle.textContent = (isShown ? "Hide resolved" : "Show resolved") + (nextCount > 0 ? ` (${nextCount})` : "");
      }
    }
  }

  async function submitInlineCommentReply(clNum, itemId) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    const item = findInlineItemById(num, idStr);
    const replyState = state.openReplyByItemId[idStr];
    if (!item || !replyState || replyState.submitting) return;

    const ta = document.getElementById(`commentReplyInput-${idStr}`);
    const replyText = String((ta ? ta.value : replyState.draft) || "").trim();
    if (!replyText) {
      showToast("Please enter a reply message before sending.");
      if (ta && typeof ta.focus === "function") ta.focus();
      return;
    }
    const targetCommentId = String(item.comment_id || "").trim();
    if (!targetCommentId) {
      replyState.error = "Gerrit comment ID is not yet synced for this card. Refresh the queue and retry.";
      insertOrReplaceInlineItemInDom(num, item, {
        forceRender: true,
        focusTextareaId: `commentReplyInput-${idStr}`,
      });
      showToast("Refresh the queue to load Gerrit comment ID before replying.");
      return;
    }

    const chkEl = document.getElementById(`commentReplyResolved-${idStr}`);
    if (chkEl) {
      replyState.unresolved = !chkEl.checked;
    }
    replyState.draft = replyText;
    replyState.submitting = true;
    replyState.error = "";
    insertOrReplaceInlineItemInDom(num, item, { forceRender: true });

    try {
      const resp = await sidecarFetch("/api/gerrit/reply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          comment_id: targetCommentId,
          file_path: item.file_path || "",
          line: item.line || 1,
          patch_set: item.patch_set || null,
          side: item.side || "REVISION",
          unresolved: Boolean(replyState.unresolved),
          message: replyText,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(data.error || "Failed to post inline reply to Gerrit");
      }

      const nowUnresolved = Boolean(data.unresolved);
      applySubmittedReplyToThreadItem(num, item, data, replyText, nowUnresolved);
      delete state.openReplyByItemId[idStr];
      invalidateClientQueueCache();

      const refreshedItem = findInlineItemById(num, idStr) || item;
      insertOrReplaceInlineItemInDom(num, refreshedItem, { forceRender: true });
      const stateWord = nowUnresolved ? "Unresolved" : "Resolved";
      const locLabel = `${item.file_path || "/COMMIT_MSG"}:L${item.line || 1}`;
      document.getElementById("drawerStatus").textContent =
        `Posted reply on ag/${num} (${locLabel}, ${stateWord}, notify: OWNER)`;
      showToast(`✓ Sent reply on ${locLabel} (${stateWord}, notify: OWNER)!`);
    } catch (err) {
      if (state.openReplyByItemId[idStr]) {
        state.openReplyByItemId[idStr].submitting = false;
        state.openReplyByItemId[idStr].error = err.message || String(err);
      }
      insertOrReplaceInlineItemInDom(num, item, {
        forceRender: true,
        focusTextareaId: `commentReplyInput-${idStr}`,
      });
      showToast("Reply failed: " + (err.message || err));
    }
  }

  const NON_FUNCTION_KEYWORDS = new Set([
    "if", "else", "for", "while", "switch", "case", "catch", "return",
    "sizeof", "alignof", "decltype", "static_assert", "new", "delete",
  ]);

  const _HUNK_HEADER_SYMBOL_RE = /^@@[^@]+@@\s*(.+)$/;
  const _PY_SYMBOL_RE = /^(?:async\s+)?(?:def|class)\s+([^\s(:]+)\s*[:(]/;
  const _GO_SYMBOL_RE = /^func\s+(?:\([^)]*\)\s*)?([^\s([<]+)/;
  const _RUST_SYMBOL_RE = /^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:fn|struct|impl)\s+([^\s(<{]+)/;
  const _JS_FN_SYMBOL_RE = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([^\s(<]+)\s*\(/;
  const _CPP_FN_SYMBOL_RE = /^(?:[\w:<>*&~\[\],\s]+?\s+)?([~A-Za-z_\u0080-\uffff\ud800-\udfff][\w\u0080-\uffff\ud800-\udfff:]*)\s*\([^;]*$/;
  const _CPP_PRE_PAREN_ALLOWED = (function() {
    const tbl = new Uint8Array(128);
    const allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:<>*&~[], \t\r\n";
    for (let i = 0; i < allowed.length; i++) {
      tbl[allowed.charCodeAt(i)] = 1;
    }
    return tbl;
  })();

  function extractSymbolFromCodeLine(rawLine, skipDiffPrefix = false) {
    let text = String(rawLine || "");
    if (!text) return "";
    if (text.charCodeAt(0) === 64 && text.charCodeAt(1) === 64) {
      const hunkMatch = text.match(_HUNK_HEADER_SYMBOL_RE);
      if (hunkMatch) text = hunkMatch[1];
    }
    const len = text.length;
    let startIdx = (skipDiffPrefix && (text.charCodeAt(0) === 43 || text.charCodeAt(0) === 32)) ? 1 : 0;
    while (startIdx < len && text.charCodeAt(startIdx) <= 32) startIdx++;
    if (startIdx >= len) return "";
    const ch0 = text.charCodeAt(startIdx);
    if (
      ch0 === 35 ||
      ch0 === 42 ||
      ch0 === 125 ||
      ch0 === 93 ||
      ch0 === 59 ||
      (ch0 === 47 && (text.charCodeAt(startIdx + 1) === 47 || text.charCodeAt(startIdx + 1) === 42))
    ) {
      return "";
    }
    if (
      (ch0 === 114 && (text.startsWith("return ", startIdx) || text.startsWith("return;", startIdx) || text.startsWith("return(", startIdx))) ||
      (ch0 === 105 && (text.startsWith("if ", startIdx) || text.startsWith("if(", startIdx))) ||
      (ch0 === 102 && (text.startsWith("for ", startIdx) || text.startsWith("for(", startIdx))) ||
      (ch0 === 119 && (text.startsWith("while ", startIdx) || text.startsWith("while(", startIdx))) ||
      (ch0 === 115 && (text.startsWith("switch ", startIdx) || text.startsWith("switch(", startIdx))) ||
      (ch0 === 99 && (text.startsWith("catch ", startIdx) || text.startsWith("catch(", startIdx) || text.startsWith("case ", startIdx))) ||
      (ch0 === 101 && (text.startsWith("else ", startIdx) || text.startsWith("else{", startIdx)))
    ) {
      return "";
    }
    if (
      (ch0 === 100 || ch0 === 99 || ch0 === 97) &&
      (text.startsWith("def ", startIdx) || text.startsWith("class ", startIdx) || text.startsWith("async ", startIdx))
    ) {
      const pyMatch = text.slice(startIdx).trim().match(_PY_SYMBOL_RE);
      if (pyMatch) return pyMatch[1];
    }
    if (ch0 === 102 && text.startsWith("func ", startIdx)) {
      const goMatch = text.slice(startIdx).trim().match(_GO_SYMBOL_RE);
      if (goMatch) return goMatch[1];
    }
    if (
      (ch0 === 112 || ch0 === 102 || ch0 === 115 || ch0 === 105 || ch0 === 97 || ch0 === 117) &&
      (text.startsWith("fn ", startIdx) || text.startsWith("pub", startIdx) || text.startsWith("struct ", startIdx) ||
       text.startsWith("impl ", startIdx) || text.startsWith("async ", startIdx) || text.startsWith("unsafe ", startIdx))
    ) {
      const rustMatch = text.slice(startIdx).trim().match(_RUST_SYMBOL_RE);
      if (rustMatch) return rustMatch[1];
    }
    if (
      (ch0 === 102 || ch0 === 101 || ch0 === 100 || ch0 === 97) &&
      (text.startsWith("function", startIdx) || text.startsWith("export ", startIdx) || text.startsWith("default ", startIdx) || text.startsWith("async ", startIdx))
    ) {
      const jsFnMatch = text.slice(startIdx).trim().match(_JS_FN_SYMBOL_RE);
      if (jsFnMatch) return jsFnMatch[1];
    }
    let parenIdx = -1;
    for (let i = startIdx; i < len; i++) {
      const c = text.charCodeAt(i);
      if (c === 40) {
        parenIdx = i;
        break;
      }
      if (c < 128 && _CPP_PRE_PAREN_ALLOWED[c] === 0) {
        return "";
      }
    }
    if (parenIdx > startIdx && text.indexOf(";", parenIdx + 1) === -1) {
      const cppMatch = text.slice(startIdx).trim().match(_CPP_FN_SYMBOL_RE);
      if (cppMatch) {
        const cand = cppMatch[1];
        const base = cand.includes("::") ? cand.split("::").pop() : cand;
        if (base && !NON_FUNCTION_KEYWORDS.has(base)) return cand;
      }
    }
    return "";
  }

  function resolveEnclosingSymbolForDiffLine(clNum, filePath, lineIdx, targetLine, side) {
    if (!filePath || filePath === "/COMMIT_MSG") return "";
    const bundle = state.diffsByCl[Number(clNum)];
    const files = (bundle && bundle.files) || [];
    const fileEntry = files.find(f => (f.file || "") === filePath);
    if (!fileEntry || !Array.isArray(fileEntry.lines) || fileEntry.lines.length === 0) {
      return "";
    }
    const lines = fileEntry.lines;
    const isParent = String(side || "").toUpperCase() === "PARENT";
    let startScanIdx = -1;
    if (lineIdx != null && !Number.isNaN(Number(lineIdx))) {
      startScanIdx = Math.min(lines.length - 1, Math.max(0, Number(lineIdx)));
    } else {
      const tgt = Number(targetLine || 1);
      for (let i = 0; i < lines.length; i++) {
        const rawVal = isParent
          ? (lines[i].old || lines[i].old_ln)
          : (lines[i].new || lines[i].new_ln);
        const n = Number(rawVal || 0);
        if (!Number.isNaN(n) && n >= tgt) {
          startScanIdx = i;
          break;
        }
      }
      if (startScanIdx < 0) startScanIdx = lines.length - 1;
    }
    for (let idx = startScanIdx; idx >= 0; idx--) {
      const entry = lines[idx];
      const ltype = entry.type || "";
      const rawText = String(entry.text || "");
      if (ltype === "hunk") {
        return extractSymbolFromCodeLine(rawText);
      }
      if ((ltype === "del" && !isParent) || (ltype === "add" && isParent)) {
        continue;
      }
      const codeBody = /^[+\- ]/.test(rawText) ? rawText.slice(1) : rawText;
      if (codeBody.length - codeBody.trimStart().length > 8) {
        continue;
      }
      const sym = extractSymbolFromCodeLine(codeBody);
      if (sym) return sym;
    }
    return "";
  }

