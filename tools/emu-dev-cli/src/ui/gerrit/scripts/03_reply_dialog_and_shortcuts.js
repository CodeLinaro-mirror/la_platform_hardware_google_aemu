  const CR_STAGE_BUTTON_IDS = [
    { val: -2, id: "stageCrNeg2" },
    { val: -1, id: "stageCrNeg1" },
    { val: 0, id: "stageCrZero" },
    { val: 1, id: "stageCrPlus1" },
    { val: 2, id: "stageCrPlus2" },
  ];

  const V_STAGE_BUTTON_IDS = [
    { val: -1, id: "stageVNeg1" },
    { val: 0, id: "stageVZero" },
    { val: 1, id: "stageVPlus1" },
  ];

  const PV_STAGE_BUTTON_IDS = [
    { val: -1, id: "stagePvNeg1" },
    { val: 0, id: "stagePvZero" },
    { val: 1, id: "stagePvPlus1" },
  ];

  const PERF_STAGE_BUTTON_IDS = [
    { val: -1, id: "stagePerfNeg1" },
    { val: 0, id: "stagePerfZero" },
    { val: 1, id: "stagePerfPlus1" },
  ];

  const LINT_STAGE_BUTTON_IDS = [
    { val: -1, id: "stageLintNeg1" },
    { val: 0, id: "stageLintZero" },
    { val: 1, id: "stageLintPlus1" },
  ];

  const BP_STAGE_BUTTON_IDS = [
    { val: 0, id: "stageBpZero" },
    { val: 1, id: "stageBpPlus1" },
    { val: 2, id: "stageBpPlus2" },
  ];

  const API_STAGE_BUTTON_IDS = [
    { val: -1, id: "stageApiNeg1" },
    { val: 0, id: "stageApiZero" },
    { val: 1, id: "stageApiPlus1" },
  ];

  const OO_STAGE_BUTTON_IDS = [
    { val: 0, id: "stageOoZero" },
    { val: 1, id: "stageOoPlus1" },
  ];

  const RS_STAGE_BUTTON_IDS = [
    { val: 0, id: "stageRsZero" },
    { val: 1, id: "stageRsPlus1" },
  ];

  const CQ_STAGE_BUTTON_IDS = [
    { val: 0, id: "stageCqZero" },
    { val: 1, id: "stageCqPlus1" },
    { val: 2, id: "stageCqPlus2" },
  ];

  const OSL_STAGE_BUTTON_IDS = [
    { val: -1, id: "stageOslNeg1" },
    { val: 0, id: "stageOslZero" },
    { val: 1, id: "stageOslPlus1" },
  ];

  const TRUST_STAGE_BUTTON_IDS = [
    { val: 0, id: "stageTrustZero" },
    { val: 1, id: "stageTrustPlus1" },
  ];

  function getOrderedCheckedClNumbers() {
    if (!state.checkedClNumbers || state.checkedClNumbers.size === 0) return [];
    const orderedVisible = getOrderedVisibleCommits(getVisibleStacks()).map(x => Number(x.commit.number));
    const seen = new Set();
    const result = [];
    orderedVisible.forEach(num => {
      if (state.checkedClNumbers.has(num) && !seen.has(num)) {
        seen.add(num);
        result.push(num);
      }
    });
    state.checkedClNumbers.forEach(rawNum => {
      const n = Number(rawNum);
      if (n > 0 && !seen.has(n)) {
        seen.add(n);
        result.push(n);
      }
    });
    return result;
  }

  const _EMPTY_SINGLE_VOTE_TARGET = Object.freeze({
    isBatch: false,
    clNums: Object.freeze([]),
    primaryCl: null,
  });

  function resolveVoteTargets(commitsOverride = null) {
    const checkedNums = getOrderedCheckedClNumbers();
    if (checkedNums.length > 0) {
      const firstCl = findPgChangeByNumber(checkedNums[0]);
      return { isBatch: true, clNums: checkedNums, primaryCl: firstCl };
    }
    const commits = Array.isArray(commitsOverride) ? commitsOverride : getActiveCommitsForDiff();
    const selNum = +state.selectedClNumber;
    let primary = commits[0] || null;
    if (commits.length > 1 && selNum) {
      for (let i = 0; i < commits.length; i++) {
        if (+commits[i].number === selNum) {
          primary = commits[i];
          break;
        }
      }
    }
    if (!primary) return _EMPTY_SINGLE_VOTE_TARGET;
    const pNum = +primary.number;
    if (
      primary._singleVoteTarget &&
      primary._singleVoteTarget.primaryCl === primary &&
      primary._singleVoteTarget.clNums[0] === pNum
    ) {
      return primary._singleVoteTarget;
    }
    const target = {
      isBatch: false,
      clNums: [pNum],
      primaryCl: primary,
    };
    primary._singleVoteTarget = target;
    return target;
  }

  function getEffectiveCurrentViewerPr(c) {
    if (!c || !c.viewer_presubmit_ready) return 0;
    const pv = getClPresubmitDisplay(c);
    return (pv.status === "queued" || pv.status === "running") ? 1 : 0;
  }

  const PG_REPLY_DRAFT_PREFIX = "pg_reply_draft_";
  const _replyDraftByClCache = new Map();

  function getPgReplyDraftStorageKey(clNum) {
    return `${PG_REPLY_DRAFT_PREFIX}${clNum || "batch"}`;
  }

  function loadPgReplyDraft(clNum) {
    const cacheId = clNum ? String(clNum) : "batch";
    if (_replyDraftByClCache.has(cacheId)) {
      return _replyDraftByClCache.get(cacheId);
    }
    try {
      const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return null;
      const key = getPgReplyDraftStorageKey(cacheId);
      const raw = ls.getItem(key);
      if (!raw) {
        _replyDraftByClCache.set(cacheId, null);
        return null;
      }
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        _replyDraftByClCache.set(cacheId, parsed);
        return parsed;
      }
    } catch (_e) {}
    _replyDraftByClCache.set(cacheId, null);
    return null;
  }

  function savePgReplyDraft(clNum, draftData) {
    const cacheId = clNum ? String(clNum) : "batch";
    try {
      const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) {
        _replyDraftByClCache.delete(cacheId);
        return;
      }
      const key = getPgReplyDraftStorageKey(cacheId);
      if (!draftData || (!draftData.comment && !draftData.hasVotes)) {
        _replyDraftByClCache.set(cacheId, null);
        ls.removeItem(key);
        return;
      }
      const toSave = Object.assign({}, draftData, {
        updatedAt: Date.now(),
      });
      _replyDraftByClCache.set(cacheId, toSave);
      ls.setItem(key, JSON.stringify(toSave));
    } catch (_e) {
      _replyDraftByClCache.delete(cacheId);
    }
  }

  function deletePgReplyDraft(clNum) {
    const cacheId = clNum ? String(clNum) : "batch";
    _replyDraftByClCache.set(cacheId, null);
    try {
      const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return;
      const key = getPgReplyDraftStorageKey(cacheId);
      ls.removeItem(key);
    } catch (_e) {}
  }

  function updatePgReplyDialogAutosaveBadge(status = "saved", includeTimestamp = false) {
    const badge = document.getElementById("replyDialogAutosaveBadge");
    if (!badge) return;
    if (!includeTimestamp && badge._lastAutosaveStatus === status) return;
    badge._lastAutosaveStatus = status;
    badge.className = `pg-draft-autosave-badge is-${status}`;
    if (badge.classList && typeof badge.classList.add === "function") {
      badge.classList.remove("is-saved", "is-unsaved", "is-saving");
      badge.classList.add("pg-draft-autosave-badge", `is-${status}`);
    }
    if (status === "unsaved") {
      badge.textContent = "● Unsaved";
      badge.title = "Unsaved review message (autosaves automatically)";
    } else if (status === "saving") {
      badge.textContent = "Saving…";
      badge.title = "Autosaving review message draft…";
    } else {
      badge.textContent = "✓ Saved";
      badge.title = includeTimestamp
        ? `Review message automatically saved at ${new Date().toLocaleTimeString()}`
        : "Review message automatically saved";
    }
  }

  function schedulePgReplyDialogDraftAutosave(clNum = null) {
    const num = clNum || (state.selectedClNumber ? Number(state.selectedClNumber) : "batch");
    updatePgReplyDialogAutosaveBadge("unsaved");
    syncPgReplyDraftDiscardButton();
    if (state.replyDialogAutosaveTimer) {
      clearTimeout(state.replyDialogAutosaveTimer);
    }
    state.replyDialogAutosaveTimer = setTimeout(() => {
      flushPgReplyDialogDraftAutosave(num);
    }, 500);
  }

  function flushPgReplyDialogDraftAutosave(clNum = null) {
    if (state.replyDialogAutosaveTimer) {
      clearTimeout(state.replyDialogAutosaveTimer);
      state.replyDialogAutosaveTimer = null;
    }
    const num = clNum || (state.selectedClNumber ? Number(state.selectedClNumber) : "batch");
    const comment = (state.stagedVote && state.stagedVote.comment) || "";
    const hasVotes = Boolean(
      state.stagedVote && (
        state.stagedVote.cr !== null ||
        state.stagedVote.v !== null ||
        state.stagedVote.pr !== null ||
        state.stagedVote.as !== null ||
        (state.stagedVote.extraLabels && Object.keys(state.stagedVote.extraLabels).length > 0)
      )
    );
    if (comment.trim() || hasVotes) {
      savePgReplyDraft(num, {
        clNum: num,
        comment: comment,
        cr: state.stagedVote ? state.stagedVote.cr : null,
        v: state.stagedVote ? state.stagedVote.v : null,
        pv: state.stagedVote ? state.stagedVote.pv : null,
        pr: state.stagedVote ? state.stagedVote.pr : null,
        as: state.stagedVote ? state.stagedVote.as : null,
        extraLabels: state.stagedVote ? state.stagedVote.extraLabels : {},
        hasVotes,
      });
      updatePgReplyDialogAutosaveBadge("saved");
    } else {
      deletePgReplyDraft(num);
      updatePgReplyDialogAutosaveBadge("saved");
    }
    syncPgReplyDraftDiscardButton();
    if (typeof num === "number" && num > 0) {
      updateReplyButtonDraftsBadge(num);
      syncReplyDraftsSectionDom(num);
    }
  }

  function syncPgReplyDraftDiscardButton() {
    const btn = document.getElementById("btnDiscardReplyCommentDraft");
    if (!btn) return;
    const curVal = (state.stagedVote && state.stagedVote.comment) || "";
    const nextDisp = curVal.trim() ? "" : "none";
    if (btn.style.display !== nextDisp) {
      btn.style.display = nextDisp;
    }
  }

  function restorePgReplyDraftForCl(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return false;
    const draft = loadPgReplyDraft(num);
    if (!draft) return false;
    let restoredAny = false;
    if (draft.comment && (!state.stagedVote.comment || !state.stagedVote.comment.trim())) {
      state.stagedVote.comment = draft.comment;
      const inp = document.getElementById("stagedVoteCommentInput");
      if (inp) inp.value = draft.comment;
      restoredAny = true;
    }
    if (draft.cr !== null && state.stagedVote.cr === null) {
      state.stagedVote.cr = draft.cr;
      restoredAny = true;
    }
    if (draft.v !== null && state.stagedVote.v === null) {
      state.stagedVote.v = draft.v;
      restoredAny = true;
    }
    if (draft.pr !== null && state.stagedVote.pr === null) {
      state.stagedVote.pr = draft.pr;
      restoredAny = true;
    }
    if (draft.as !== null && state.stagedVote.as === null) {
      state.stagedVote.as = draft.as;
      restoredAny = true;
    }
    if (draft.extraLabels && Object.keys(draft.extraLabels).length > 0) {
      state.stagedVote.extraLabels = Object.assign({}, draft.extraLabels, state.stagedVote.extraLabels || {});
      restoredAny = true;
    }
    syncPgReplyDraftDiscardButton();
    return restoredAny;
  }

  function discardReplyCommentDraft(clNum = null, skipConfirm = false) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const curVal = (state.stagedVote && state.stagedVote.comment) || "";
    if (!curVal.trim() && (!num || !loadPgReplyDraft(num))) {
      return;
    }
    const executeDiscard = () => {
      if (num) {
        deletePgReplyDraft(num);
      }
      deletePgReplyDraft("batch");
      state.stagedVote.comment = "";
      const inp = document.getElementById("stagedVoteCommentInput");
      if (inp) inp.value = "";
      updatePgReplyDialogAutosaveBadge("saved");
      syncPgReplyDraftDiscardButton();
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
      syncPgReplyQuickChipsActiveState();
      if (num) {
        updateReplyButtonDraftsBadge(num);
        syncReplyDraftsSectionDom(num);
      }
      if (typeof showToast === "function") {
        showToast("Discarded draft review message");
      }
    };
    if (skipConfirm || curVal.trim().length < 30) {
      executeDiscard();
    } else {
      openPgConfirmDiscardDialog({
        title: "Discard Draft Review Message",
        message: "Are you sure you want to discard your draft review message? This cannot be undone.",
        confirmLabel: "Discard Message",
        onConfirm: executeDiscard,
      });
    }
  }

  function isStagedVoteStateClean(sv) {
    return Boolean(
      sv &&
      !sv.comment &&
      sv.cr === null && sv.v === null && sv.pv === null &&
      sv.pr === null && sv.as === null && sv.perf === null &&
      sv.lint === null && sv.bp === null && sv.api === null &&
      sv.oo === null && sv.rs === null && sv.cq === null &&
      sv.osl === null && sv.trust === null && !sv.noteOpen &&
      !hasAnyOwnProp(sv.extraLabels)
    );
  }

  function ensureStagedVoteContextSynced(commitsOverride = null) {
    const resolvedTargets = resolveVoteTargets(commitsOverride);
    const { isBatch, primaryCl } = resolvedTargets;
    const nextKey = isBatch
      ? "batch"
      : (primaryCl
          ? ((primaryCl._stagedContextKey && primaryCl._stagedContextNum === primaryCl.number)
              ? primaryCl._stagedContextKey
              : (primaryCl._stagedContextNum = primaryCl.number, primaryCl._stagedContextKey = `single:${primaryCl.number}`))
          : "empty");
    if (state.stagedVote.contextKey !== nextKey) {
      const prevSv = state.stagedVote;
      const prevIsClean = !state.replyDialogAutosaveTimer && isStagedVoteStateClean(prevSv);
      if (prevIsClean) {
        const fastDraft = (!isBatch && primaryCl)
          ? loadPgReplyDraft(primaryCl.number)
          : (isBatch ? loadPgReplyDraft("batch") : null);
        if (!fastDraft && state._stagedVoteDomCleanSynced) {
          prevSv.contextKey = nextKey;
          return resolvedTargets;
        }
      }
      const hasPrevPending = !prevIsClean && Boolean(
        state.replyDialogAutosaveTimer ||
        (prevSv && (
          (prevSv.comment && prevSv.comment.trim()) ||
          prevSv.cr !== null ||
          prevSv.v !== null ||
          prevSv.pv !== null ||
          prevSv.pr !== null ||
          prevSv.as !== null ||
          (prevSv.extraLabels && Object.keys(prevSv.extraLabels).length > 0)
        ))
      );
      if (hasPrevPending) {
        if (prevSv.contextKey && prevSv.contextKey.startsWith("single:")) {
          const prevCl = Number(prevSv.contextKey.replace("single:", ""));
          if (prevCl) {
            flushPgReplyDialogDraftAutosave(prevCl);
          }
        } else if (prevSv.contextKey === "batch") {
          flushPgReplyDialogDraftAutosave("batch");
        }
      }

      state.stagedVote = {
        contextKey: nextKey,
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
      };

      let hadRestoredDraft = false;
      if (!isBatch && primaryCl) {
        const draft = loadPgReplyDraft(primaryCl.number);
        if (draft) {
          hadRestoredDraft = true;
          state.stagedVote.comment = draft.comment || "";
          if (draft.cr !== null) state.stagedVote.cr = draft.cr;
          if (draft.v !== null) state.stagedVote.v = draft.v;
          if (draft.pv !== null) state.stagedVote.pv = draft.pv;
          if (draft.pr !== null) state.stagedVote.pr = draft.pr;
          if (draft.as !== null) state.stagedVote.as = draft.as;
          if (draft.extraLabels) state.stagedVote.extraLabels = Object.assign({}, draft.extraLabels);
        }
      } else if (isBatch) {
        const batchDraft = loadPgReplyDraft("batch");
        if (batchDraft) {
          hadRestoredDraft = true;
          state.stagedVote.comment = batchDraft.comment || "";
          if (batchDraft.cr !== null) state.stagedVote.cr = batchDraft.cr;
          if (batchDraft.v !== null) state.stagedVote.v = batchDraft.v;
          if (batchDraft.pr !== null) state.stagedVote.pr = batchDraft.pr;
          if (batchDraft.as !== null) state.stagedVote.as = batchDraft.as;
          if (batchDraft.extraLabels) state.stagedVote.extraLabels = Object.assign({}, batchDraft.extraLabels);
        }
      }

      const noteInp = document.getElementById("stagedVoteCommentInput");
      if (noteInp) {
        const nextComment = state.stagedVote.comment || "";
        if (noteInp.value !== nextComment) {
          noteInp.value = nextComment;
        }
      }
      syncPgReplyDraftDiscardButton();
      updatePgReplyDialogAutosaveBadge("saved");
      state._stagedVoteDomCleanSynced = !hadRestoredDraft;
    }
    return resolvedTargets;
  }

  const PG_CR_SCORE_DESCRIPTIONS = {
    "-2": "Do not submit",
    "-1": "I would prefer that you didn't submit this",
    "0": "No score",
    "1": "Looks good to me, but someone else must approve",
    "2": "Looks good to me, approved",
  };

  const PG_V_SCORE_DESCRIPTIONS = {
    "-1": "Fails",
    "0": "No score",
    "1": "Verified",
  };

  const PG_PV_SCORE_DESCRIPTIONS = {
    "-1": "Presubmit verification failed",
    "0": "No score",
    "1": "Presubmit verified",
  };

  const PG_PERF_SCORE_DESCRIPTIONS = {
    "-1": "Performance regression / needs work",
    "0": "No score",
    "1": "Performance verified / acceptable",
  };

  const PG_LINT_SCORE_DESCRIPTIONS = {
    "-1": "Lint issues found / fix required",
    "0": "No score",
    "1": "Lint clean / verified",
  };

  const PG_BP_SCORE_DESCRIPTIONS = {
    "0": "Do not bypass",
    "1": "Bypass presubmit (break-glass)",
    "2": "Bypass presubmit (emergency override)",
  };

  const PG_API_SCORE_DESCRIPTIONS = {
    "-1": "API changes rejected",
    "0": "No score",
    "1": "API approved",
  };

  const PG_OO_SCORE_DESCRIPTIONS = {
    "0": "No override",
    "1": "Override OWNERS approval",
  };

  const PG_RS_SCORE_DESCRIPTIONS = {
    "0": "No score",
    "1": "Rubber stamp approved (trivial approval)",
  };

  const PG_CQ_SCORE_DESCRIPTIONS = {
    "0": "No score",
    "1": "Dry run presubmit",
    "2": "Submit to commit queue",
  };

  const PG_OSL_SCORE_DESCRIPTIONS = {
    "-1": "Open source licensing issue",
    "0": "No score",
    "1": "Open source licensing compliant",
  };

  const PG_TRUST_SCORE_DESCRIPTIONS = {
    "0": "No trust score",
    "1": "Trusted change",
  };

  const PG_SUBMIT_TYPE_LABELS = {
    REBASE_ALWAYS: "Rebase Always",
    REBASE_IF_NECESSARY: "Rebase If Necessary",
    MERGE_IF_NECESSARY: "Merge If Necessary",
    FAST_FORWARD_ONLY: "Fast Forward Only",
    CHERRY_PICK: "Cherry Pick",
    REBASE_ON_BEHALF_OF_UPLOADER: "Rebase On Behalf Of Uploader",
  };

  const _PG_EMPTY_ARRAY = Object.freeze([]);

  function getPendingReplyDialogDraft(clNum) {
    const num = Number(clNum);
    if (!num) return null;
    const sv = state.stagedVote;
    const rawComment = (sv && sv.comment) ? sv.comment : "";
    if (rawComment && sv.contextKey === `single:${num}`) {
      const comment = rawComment.trim();
      if (comment) {
        return { message: comment, updatedAt: Date.now() };
      }
    }
    const saved = loadPgReplyDraft(num);
    if (saved && saved.comment && saved.comment.trim()) {
      return { message: saved.comment.trim(), updatedAt: saved.updatedAt || null };
    }
    return null;
  }

  function getPendingDraftsForCl(clNum) {
    const num = Number(clNum);
    if (!num) return _PG_EMPTY_ARRAY;
    let drafts = null;

    // 1. Saved inline code comment drafts
    const items = state.inlineCommentsByCl && state.inlineCommentsByCl[num];
    if (items && items.length > 0) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item && item.kind === "draft") {
          if (!drafts) drafts = [];
          drafts.push({
            id: item.id,
            kind: "draft",
            isReply: false,
            clNum: num,
            file_path: item.file_path || "/COMMIT_MSG",
            line: item.line || item.end_line || 1,
            start_line: item.start_line,
            end_line: item.end_line,
            side: item.side || "REVISION",
            unresolved: item.unresolved !== false,
            message: item.message || "",
            code_snippet: item.code_snippet || "",
          });
        }
      }
    }

    // 2. Open or saved reply composer drafts
    const openReplies = state.openReplyByItemId;
    if (hasAnyOwnProp(openReplies)) {
      for (const itemId in openReplies) {
        if (!Object.prototype.hasOwnProperty.call(openReplies, itemId)) continue;
        const entry = openReplies[itemId];
        if (!entry) continue;
        const item = findInlineItemById(num, itemId);
        if (!item) continue;
        const msg = String(entry.draft || "").trim();
        if (msg) {
          if (!drafts) drafts = [];
          drafts.push({
            id: itemId,
            kind: "reply",
            isReply: true,
            clNum: num,
            file_path: item.file_path || "/COMMIT_MSG",
            line: item.line || 1,
            side: item.side || "REVISION",
            unresolved: Boolean(entry.unresolved),
            message: msg,
            targetAuthor: item.root_author_name || item.author_name || "reviewer",
            comment_id: item.comment_id || "",
            rawItem: item,
          });
        }
      }
    }

    // 3. Pending top-level reply dialog draft review message
    const topDraft = getPendingReplyDialogDraft(num);
    if (topDraft && topDraft.message && topDraft.message.trim()) {
      if (!drafts) drafts = [];
      drafts.push({
        id: `reply_dialog_draft_${num}`,
        kind: "reply_dialog",
        isReply: false,
        clNum: num,
        file_path: "/REPLY_MSG",
        line: 1,
        message: topDraft.message.trim(),
        targetAuthor: "author",
        updatedAt: topDraft.updatedAt || null,
      });
    }

    if (drafts && drafts.length > 0) {
      state._hadNonEmptyReplyDraftsSync = true;
    }
    return drafts || _PG_EMPTY_ARRAY;
  }

  function getPendingDraftsForFile(clNum, filePath) {
    const num = Number(clNum || state.selectedClNumber);
    const targetPath = String(filePath || "");
    if (!num || !targetPath) return [];
    if (targetPath !== "/REPLY_MSG") {
      const items = state.inlineCommentsByCl && state.inlineCommentsByCl[num];
      const openReplies = state.openReplyByItemId;
      let hasOpenReply = false;
      if (openReplies) {
        for (const k in openReplies) {
          if (Object.prototype.hasOwnProperty.call(openReplies, k) && openReplies[k]) {
            hasOpenReply = true;
            break;
          }
        }
      }
      if ((!items || items.length === 0) && !hasOpenReply) {
        return _PG_EMPTY_ARRAY;
      }
    }
    const drafts = (typeof getPendingDraftsForCl === "function") ? getPendingDraftsForCl(num) : [];
    return drafts.filter(d => (d.file_path || "/COMMIT_MSG") === targetPath);
  }

  function renderPgReplyDraftsListHtml(clNum, drafts) {
    if (!Array.isArray(drafts) || drafts.length === 0) {
      return `<div class="pg-reply-drafts-empty">No draft comments pending. Highlight code in diff or click Reply to add comments.</div>`;
    }
    return drafts.map(d => {
      const isReplyMsg = d.kind === "reply_dialog";
      const loc = isReplyMsg ? "General Review Message" : `${d.file_path || "/COMMIT_MSG"}:${d.line || 1}`;
      const unresPill = isReplyMsg
        ? `<span class="diff-inline-status-pill sent">Message</span>`
        : (d.unresolved
            ? `<span class="diff-inline-status-pill">Unresolved</span>`
            : `<span class="diff-inline-status-pill sent">Resolved</span>`);
      const typeLabel = isReplyMsg ? "Review Message" : (d.isReply ? `Reply to ${escapeHtml(d.targetAuthor || "thread")}` : "New Comment");
      const preview = String(d.message || "").replace(/\s+/g, " ").trim();
      const snippetHtml = d.code_snippet
        ? `<div class="pg-reply-draft-snippet" title="Quoted code snippet"><code>${escapeHtml(d.code_snippet)}</code></div>`
        : "";
      const editTitle = isReplyMsg ? "Edit this message in reply dialog" : "Edit this draft in diff";
      return `<div class="pg-reply-draft-card${isReplyMsg ? " is-reply-message" : ""}" id="pgReplyDraftCard-${escapeHtml(d.id)}">
        <div class="pg-reply-draft-top">
          <div class="pg-reply-draft-meta">
            <a href="javascript:void(0)" class="pg-reply-draft-loc" onclick="jumpToDraftInDiff(${clNum}, '${escapeHtml(d.id)}')"><code>${escapeHtml(loc)}</code></a>
            <span class="pg-reply-draft-type">${escapeHtml(typeLabel)}</span>
            ${unresPill}
          </div>
          <div class="pg-reply-draft-actions">
            <button type="button" class="pg-reply-draft-edit" id="pgReplyDraftEdit-${escapeHtml(d.id)}" onclick="editReplyDialogDraft(${clNum}, '${escapeHtml(d.id)}', '${escapeHtml(d.kind)}')" title="${escapeHtml(editTitle)}">✎ Edit</button>
            <button type="button" class="pg-reply-draft-discard" onclick="discardReplyDialogDraftWithConfirm(${clNum}, '${escapeHtml(d.id)}', '${escapeHtml(d.kind)}')" title="Discard this draft">✕ Discard</button>
          </div>
        </div>
        ${snippetHtml}
        <div class="pg-reply-draft-body">${renderInlineMarkdownHtml(preview, false)}</div>
      </div>`;
    }).join("");
  }

  function syncReplyDraftsSectionDom(clNum, pendingDraftsOverride = null) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return;
    const drafts = pendingDraftsOverride || getPendingDraftsForCl(num);
    const count = drafts.length;
    if (count === 0 && !state._hadNonEmptyReplyDraftsSync) {
      return;
    }
    state._hadNonEmptyReplyDraftsSync = count > 0;
    const section = document.getElementById(`pgReplyDraftsSection-${num}`);
    const listEl = document.getElementById(`pgReplyDraftsList-${num}`);
    const countEl = document.getElementById(`pgReplyDraftsCount-${num}`);
    const footerDiscardBtn = document.getElementById("btnDiscardAllDraftsFooter");
    if (countEl) countEl.textContent = String(count);
    if (section) section.style.display = count > 0 ? "block" : "none";
    if (listEl) listEl.innerHTML = renderPgReplyDraftsListHtml(num, drafts);
    if (footerDiscardBtn) {
      footerDiscardBtn.style.display = count > 0 ? "inline-block" : "none";
      footerDiscardBtn.textContent = count > 1 ? `Discard All Drafts (${count})` : "Discard Draft";
    }
    syncPgDraftsSummaryChip(num, drafts);
  }

  function updateReplyButtonDraftsBadge(clNum, pendingDraftsOverride = null, skipSummaryChip = false) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return;
    const btn = document.getElementById("btnOpenReplyDialog");
    const drafts = pendingDraftsOverride || getPendingDraftsForCl(num);
    const count = drafts.length;
    if (count > 0) state._hadNonEmptyReplyDraftsSync = true;
    const label = count > 0 ? `Reply (${count} draft${count > 1 ? "s" : ""})` : "Reply";
    if (btn && (btn._lastDraftBadgeCount !== count || btn.textContent !== label)) {
      btn._lastDraftBadgeCount = count;
      btn.textContent = label;
      btn.classList.toggle("has-drafts", count > 0);
    }
    if (count > 0 || state._hadNonEmptyReplyDraftsSync) {
      const stickyBtn = document.getElementById(`btnStickyHeaderReply-${num}`);
      if (stickyBtn && (stickyBtn._lastDraftBadgeCount !== count || stickyBtn.textContent !== label)) {
        stickyBtn._lastDraftBadgeCount = count;
        stickyBtn.textContent = label;
        stickyBtn.classList.toggle("has-drafts", count > 0);
      }
      if (!skipSummaryChip) {
        syncPgDraftsSummaryChip(num, drafts);
      }
    }
  }

  function openPgConfirmDiscardDialog(options = {}) {
    state.confirmDiscardDialogOpen = true;
    state.confirmDiscardPendingAction = options.onConfirm || null;
    const backdrop = (typeof document !== "undefined") ? document.getElementById("pgConfirmDiscardBackdrop") : null;
    if (!backdrop) {
      if (typeof window !== "undefined" && typeof window.confirm === "function") {
        const ok = window.confirm(options.message || "Discard draft?");
        if (ok && typeof options.onConfirm === "function") {
          options.onConfirm();
        }
      }
      return;
    }

    const titleEl = document.getElementById("pgConfirmDiscardTitle");
    const subEl = document.getElementById("pgConfirmDiscardSubtitle");
    const msgEl = document.getElementById("pgConfirmDiscardMessage");
    const previewWrap = document.getElementById("pgConfirmDiscardPreviewWrap");
    const previewEl = document.getElementById("pgConfirmDiscardPreview");
    const batchListEl = document.getElementById("pgConfirmDiscardBatchList");
    const confirmBtn = document.getElementById("btnConfirmDiscardAction");

    if (titleEl) titleEl.textContent = options.title || "Discard Draft Comment";
    if (subEl) subEl.textContent = options.subtitle || "";
    if (msgEl) msgEl.textContent = options.message || "Are you sure you want to discard this draft comment? This action cannot be undone.";
    if (confirmBtn) confirmBtn.textContent = options.confirmLabel || "Discard";

    if (Array.isArray(options.batchDrafts) && options.batchDrafts.length > 0) {
      if (previewWrap) previewWrap.style.display = "none";
      if (batchListEl) {
        batchListEl.style.display = "flex";
        batchListEl.innerHTML = options.batchDrafts.map((d, idx) => {
          const loc = d.file_path ? `${d.file_path}${d.line ? `:${d.line}` : ""}` : "General comment";
          const snippet = String(d.message || d.prompt || "").replace(/\s+/g, " ").trim();
          return `<div class="pg-discard-batch-item">
            <div class="pg-discard-batch-item-meta">
              <strong>#${idx + 1}</strong> <code>${escapeHtml(loc)}</code>
              <span class="pg-discard-batch-kind">${escapeHtml(d.isReply ? "Reply" : "Draft")}</span>
            </div>
            <div class="pg-discard-batch-item-snippet">${escapeHtml(snippet.slice(0, 140))}${snippet.length > 140 ? "…" : ""}</div>
          </div>`;
        }).join("");
      }
    } else {
      if (batchListEl) {
        batchListEl.style.display = "none";
        batchListEl.innerHTML = "";
      }
      if (previewWrap) {
        if (options.previewText) {
          previewWrap.style.display = "flex";
          if (previewEl) previewEl.textContent = options.previewText;
        } else {
          previewWrap.style.display = "none";
        }
      }
    }

    backdrop.classList.add("open", "is-open");
    backdrop.setAttribute("aria-hidden", "false");
    const cancelBtn = backdrop.querySelector(".pg-btn-flat");
    if (cancelBtn) cancelBtn.focus();
  }

  function closePgConfirmDiscardDialog() {
    state.confirmDiscardDialogOpen = false;
    state.confirmDiscardPendingAction = null;
    const backdrop = (typeof document !== "undefined") ? document.getElementById("pgConfirmDiscardBackdrop") : null;
    if (backdrop) {
      backdrop.classList.remove("open", "is-open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgConfirmDiscardBackdropClick(event) {
    if (event.target && (event.target.id === "pgConfirmDiscardBackdrop" || event.target === event.currentTarget)) {
      closePgConfirmDiscardDialog();
    }
  }

  function executePgConfirmDiscard() {
    const action = state.confirmDiscardPendingAction;
    closePgConfirmDiscardDialog();
    if (typeof action === "function") {
      action();
    }
  }

  function discardReplyDialogDraftWithConfirm(clNum, draftId, draftKind) {
    const num = Number(clNum || state.selectedClNumber);
    const idStr = String(draftId || "");
    const drafts = getPendingDraftsForCl(num);
    const draft = drafts.find(d => String(d.id) === idStr) || {};
    const text = String(draft.message || draft.prompt || "").trim();
    if (!text) {
      discardReplyDialogDraft(num, idStr, draftKind);
      return;
    }
    const loc = draft.file_path ? `${draft.file_path}${draft.line ? `:${draft.line}` : ""}` : "Draft comment";
    openPgConfirmDiscardDialog({
      title: "Discard Draft Comment",
      subtitle: `ag/${num} · ${loc}`,
      message: "Are you sure you want to discard this draft comment? This action cannot be undone.",
      previewText: text,
      confirmLabel: "Discard Draft",
      onConfirm: () => {
        discardReplyDialogDraft(num, idStr, draftKind);
      }
    });
  }
  const promptDiscardReplyDialogDraft = discardReplyDialogDraftWithConfirm;

  function discardInlineDraftWithConfirm(clNum, itemId) {
    const num = Number(clNum || state.selectedClNumber);
    const idStr = String(itemId || "");
    const item = (typeof findInlineItemById === "function")
      ? findInlineItemById(num, idStr)
      : (state.inlineCommentsByCl[num] || []).find(it => String(it.id) === idStr);
    if (!item) return;
    const text = String(item.message || item.prompt || "").trim();
    if (!text) {
      discardInlineDraft(num, idStr);
      return;
    }
    const rangeStr = (item.line !== undefined && item.line !== null) ? `L${item.line}` : "General comment";
    const loc = item.file_path ? `${item.file_path}:${rangeStr}` : rangeStr;
    openPgConfirmDiscardDialog({
      title: "Discard Draft Comment",
      subtitle: `ag/${num} · ${loc}`,
      message: "Are you sure you want to discard this draft comment? This action cannot be undone.",
      previewText: text,
      confirmLabel: "Discard Draft",
      onConfirm: () => {
        discardInlineDraft(num, idStr);
      }
    });
  }
  const promptDiscardInlineDraft = discardInlineDraftWithConfirm;

  function discardAllReplyDialogDraftsWithConfirm(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return;
    const drafts = getPendingDraftsForCl(num);
    const count = drafts.length;
    if (count === 0) {
      showToast("No draft comments to discard.");
      return;
    }
    openPgConfirmDiscardDialog({
      title: "Discard All Draft Comments",
      subtitle: `ag/${num} · ${count} pending draft comment${count > 1 ? "s" : ""}`,
      message: `Are you sure you want to discard all ${count} draft comments on ag/${num}? This cannot be undone.`,
      confirmLabel: `Discard All (${count})`,
      batchDrafts: drafts,
      onConfirm: () => {
        discardAllReplyDialogDrafts(num, true);
      }
    });
  }
  const promptDiscardAllReplyDialogDrafts = discardAllReplyDialogDraftsWithConfirm;

  function discardReplyDialogDraft(clNum, draftId, draftKind) {
    const num = Number(clNum);
    const idStr = String(draftId || "");
    if (draftKind === "reply_dialog" || idStr.startsWith("reply_dialog_draft_")) {
      discardReplyCommentDraft(num, true);
      return;
    }
    if (draftKind === "draft") {
      discardInlineDraft(num, idStr);
    } else if (draftKind === "reply") {
      delete state.openReplyByItemId[idStr];
      const item = findInlineItemById(num, idStr);
      if (item) insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
    }
    const pendingDrafts = getPendingDraftsForCl(num);
    syncReplyDraftsSectionDom(num, pendingDrafts);
    updateReplyButtonDraftsBadge(num);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    showToast("Draft comment discarded.");
  }

  function discardAllReplyDialogDrafts(clNum, skipConfirm = false) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return;
    const drafts = getPendingDraftsForCl(num);
    const count = drafts.length;
    if (count === 0) {
      showToast("No draft comments to discard.");
      return;
    }
    if (!skipConfirm) {
      const backdrop = (typeof document !== "undefined") ? document.getElementById("pgConfirmDiscardBackdrop") : null;
      if (backdrop) {
        promptDiscardAllReplyDialogDrafts(num);
        return;
      }
      if (typeof window !== "undefined" && typeof window.confirm === "function") {
        const ok = window.confirm(`Discard all ${count} draft comment${count > 1 ? "s" : ""} on ag/${num}?`);
        if (!ok) return;
      }
    }
    // Discard top-level reply draft review message as well
    deletePgReplyDraft(num);
    deletePgReplyDraft("batch");
    if (state.stagedVote) {
      state.stagedVote.comment = "";
    }
    const noteInp = document.getElementById("stagedVoteCommentInput");
    if (noteInp) noteInp.value = "";
    syncPgReplyDraftDiscardButton();
    updatePgReplyDialogAutosaveBadge("saved");

    // 1. Remove all inline draft items for this CL
    const items = state.inlineCommentsByCl[num] || [];
    const draftItems = items.filter(it => it && it.kind === "draft");
    draftItems.forEach(it => {
      const el = document.getElementById(`inlineItem-${it.id}`);
      if (el && typeof el.remove === "function") el.remove();
    });
    state.inlineCommentsByCl[num] = items.filter(it => !it || it.kind !== "draft");

    // 2. Remove all open or saved reply composer drafts for this CL
    Object.keys(state.openReplyByItemId || {}).forEach(itemId => {
      const item = findInlineItemById(num, itemId);
      if (item) {
        delete state.openReplyByItemId[itemId];
        insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
      }
    });

    // 3. Update DOM & state
    syncReplyDraftsSectionDom(num, []);
    updateReplyButtonDraftsBadge(num);
    syncPgDraftsSummaryChip(num);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    showToast(`✓ Discarded all ${count} draft comment${count > 1 ? "s" : ""}.`);
  }

  function jumpToDraftInDiff(clNum, draftId) {
    const num = Number(clNum);
    const idStr = String(draftId || "");
    if (idStr.startsWith("reply_dialog_draft_")) {
      const inp = document.getElementById("stagedVoteCommentInput");
      if (inp && typeof inp.focus === "function") {
        inp.focus();
        inp.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      return;
    }
    closeReplyDialog();
    const item = findInlineItemById(num, idStr);
    if (item && item.file_path) {
      const fileBlocks = document.querySelectorAll(`.pg-file-diff-block[data-cl="${num}"]`);
      fileBlocks.forEach(fb => {
        if (fb.getAttribute("data-file-path") === item.file_path && fb.classList.contains("is-collapsed")) {
          fb.classList.remove("is-collapsed");
        }
      });
    }
    const cardEl = document.getElementById(`inlineItem-${idStr}`);
    if (cardEl) {
      cardEl.classList.remove("collapsed");
      const btn = cardEl.querySelector(".inline-collapse-btn");
      if (btn) btn.textContent = "Hide ▴";
      cardEl.scrollIntoView({ behavior: "smooth", block: "center" });
      cardEl.classList.remove("pg-chunk-focused-flash");
      void cardEl.offsetWidth;
      cardEl.classList.add("pg-chunk-focused-flash");
      setTimeout(() => cardEl.classList.remove("pg-chunk-focused-flash"), 1800);
      return;
    }
    showToast(`Draft ${idStr} is located in diff`);
  }

  function editReplyDialogDraft(clNum, draftId, draftKind) {
    const num = Number(clNum);
    const idStr = String(draftId || "");
    if (draftKind === "reply_dialog" || idStr.startsWith("reply_dialog_draft_")) {
      const inp = document.getElementById("stagedVoteCommentInput");
      if (inp && typeof inp.focus === "function") {
        inp.focus();
        inp.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      return;
    }
    closeReplyDialog();
    const item = findInlineItemById(num, idStr);
    if (!item) {
      showToast("Draft item not found in diff view.");
      return;
    }
    const fileBlocks = document.querySelectorAll(`.pg-file-diff-block[data-cl="${num}"]`);
    fileBlocks.forEach(fb => {
      if (fb.getAttribute("data-file-path") === item.file_path && fb.classList.contains("is-collapsed")) {
        fb.classList.remove("is-collapsed");
      }
    });
    if (draftKind === "draft") {
      reopenInlineBlockInComposer(num, idStr, item.draft_mode === "ask" ? "ask" : "comment", true);
    } else if (draftKind === "reply") {
      openInlineReplyComposer(num, idStr);
    }
  }

  function renderPgDraftsSummaryChipHtml(clNum, precomputedDrafts = null) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return "";
    const drafts = precomputedDrafts || getPendingDraftsForCl(num);
    const count = drafts.length;
    if (count === 0) {
      return `<button id="pgDraftsSummaryChip-${num}" type="button" class="pg-drafts-summary-chip is-hidden" style="display:none;" onclick="openReplyDialog({ focusComment: true })" title="Open Reply dialog to review drafts (Shortcut: r)"></button>`;
    }
    return `<button id="pgDraftsSummaryChip-${num}" type="button" class="pg-drafts-summary-chip has-drafts" onclick="openReplyDialog({ focusComment: true })" title="You have ${count} pending draft comment${count > 1 ? "s" : ""} on this change. Click to open Reply dialog (Shortcut: r or Shift+D)">
      <span class="pg-drafts-chip-icon">📝</span>
      <span class="pg-drafts-chip-label">${count} draft${count > 1 ? "s" : ""}</span>
    </button>`;
  }

  function syncPgDraftsSummaryChip(clNum, pendingDraftsOverride = null) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return;
    const chip = document.getElementById(`pgDraftsSummaryChip-${num}`);
    if (!chip) return;
    const drafts = pendingDraftsOverride || getPendingDraftsForCl(num);
    const count = drafts.length;
    if (count === 0 && chip._lastDraftCount === undefined && chip.classList.contains("is-hidden") && !chip.classList.contains("has-drafts")) {
      chip._lastDraftCount = 0;
      return;
    }
    if (count === 0 && chip._lastDraftCount === 0) return;
    chip._lastDraftCount = count;
    if (count > 0) {
      chip.style.display = "inline-flex";
      chip.classList.add("has-drafts");
      chip.classList.remove("is-hidden");
      chip.innerHTML = `<span class="pg-drafts-chip-icon">📝</span> <span class="pg-drafts-chip-label">${count} draft${count > 1 ? "s" : ""}</span>`;
      chip.title = `You have ${count} pending draft comment${count > 1 ? "s" : ""} on this change. Click to open Reply dialog (Shortcut: r or Shift+D)`;
    } else {
      chip.style.display = "none";
      chip.classList.remove("has-drafts");
      chip.classList.add("is-hidden");
    }
  }

  function navigatePgDraftComment(dir = "next", clNum = null) {
    const num = Number(clNum || state.selectedClNumber);
    const drafts = getPendingDraftsForCl(num);
    if (!drafts || drafts.length === 0) {
      showToast("No pending draft comments on this change.");
      return;
    }
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) {
      openReplyDialog({ focusComment: false });
      return;
    }
    const draftElements = Array.from(changeView.querySelectorAll(
      ".diff-inline-comment.draft-item, .diff-inline-saved-reply-draft, .diff-inline-composer"
    )).filter(el => {
      const parentInline = el.closest(".diff-inline-comment");
      if (parentInline && parentInline.dataset.cl && Number(parentInline.dataset.cl) !== num) {
        return false;
      }
      return true;
    });

    if (draftElements.length === 0) {
      openReplyDialog({ focusComment: false });
      showToast(`Showing ${drafts.length} draft${drafts.length > 1 ? "s" : ""} in Reply dialog.`);
      return;
    }

    const container = document.getElementById("diffPane") || document.getElementById("diffScroll") || window;
    const viewTop = container.scrollTop !== undefined ? container.scrollTop : 0;
    let currentIndex = -1;
    draftElements.forEach((el, idx) => {
      if ((el.offsetTop || 0) <= viewTop + 60) currentIndex = idx;
    });
    if (currentIndex === -1) currentIndex = 0;

    let targetIndex = currentIndex;
    if (dir === "next") {
      const currTop = draftElements[currentIndex].offsetTop || 0;
      targetIndex = Math.min(
        draftElements.length - 1,
        currentIndex + (currTop <= viewTop + 15 ? 1 : 0)
      );
    } else if (dir === "prev") {
      targetIndex = Math.max(0, currentIndex - 1);
    }

    const targetEl = draftElements[targetIndex];
    if (targetEl && typeof targetEl.scrollIntoView === "function") {
      const parentBlock = targetEl.closest(".pg-file-diff-block");
      if (parentBlock && parentBlock.classList.contains("is-collapsed")) {
        parentBlock.classList.remove("is-collapsed");
      }
      const parentInline = targetEl.closest(".diff-inline-comment");
      if (parentInline && parentInline.classList.contains("collapsed")) {
        parentInline.classList.remove("collapsed");
        const btn = parentInline.querySelector(".inline-collapse-btn");
        if (btn) btn.textContent = "Hide ▴";
      }
      targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
      targetEl.classList.remove("pg-chunk-focused-flash");
      void targetEl.offsetWidth;
      targetEl.classList.add("pg-chunk-focused-flash");
      setTimeout(() => targetEl.classList.remove("pg-chunk-focused-flash"), 1400);
      showToast(`Draft ${targetIndex + 1} of ${draftElements.length} (Shift+D)`);
    } else {
      openReplyDialog({ focusComment: false });
    }
  }

  function quickReplyInlineComment(clNum, itemId, quickText) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    const item = findInlineItemById(num, idStr);
    if (!item) return;
    if (idStr.startsWith("srv-thread-")) {
      state.collapsedInlineIds.delete(idStr);
    }
    item.collapsed = false;
    state.openReplyByItemId[idStr] = {
      draft: quickText,
      unresolved: false,
      submitting: false,
      error: "",
      isSavedDraft: true,
    };
    insertOrReplaceInlineItemInDom(num, item, {
      forceRender: true,
      focusTextareaId: `commentReplyInput-${idStr}`,
    });
    updateReplyButtonDraftsBadge(num);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    showToast(`Staged '${quickText}' (Resolved) for ag/${num} thread.`);
  }

  function setInlineReplyQuickText(safeId, clNum, text) {
    const ta = document.getElementById(`commentReplyInput-${safeId}`);
    if (ta) {
      ta.value = text;
      onInlineReplyDraftInput(safeId, text);
    }
    const chk = document.getElementById(`commentReplyResolved-${safeId}`);
    if (chk && !chk.checked) {
      chk.checked = true;
      onInlineReplyResolvedChange(clNum, safeId, true);
    }
  }

  function saveInlineReplyAsDraft(clNum, itemId) {
    const num = Number(clNum);
    const idStr = String(itemId || "");
    if (state.replyAutosaveTimers && state.replyAutosaveTimers[idStr]) {
      clearTimeout(state.replyAutosaveTimers[idStr]);
      delete state.replyAutosaveTimers[idStr];
    }
    const replyState = state.openReplyByItemId[idStr];
    if (replyState && replyState.submitting) return;
    const ta = document.getElementById(`commentReplyInput-${idStr}`);
    const text = String((ta ? ta.value : (replyState && replyState.draft)) || "").trim();
    if (!text) {
      showToast("Please enter a reply draft message or click Cancel.");
      return;
    }
    const chk = document.getElementById(`commentReplyResolved-${idStr}`);
    const unresolved = chk ? !chk.checked : false;
    state.openReplyByItemId[idStr] = {
      draft: text,
      unresolved,
      submitting: false,
      error: "",
      isSavedDraft: true,
      saveStatus: "saved",
      savedAt: Date.now(),
    };
    updatePgReplyDraftAutosaveBadge(idStr, "saved");
    const item = findInlineItemById(num, idStr);
    if (item) {
      item.collapsed = false;
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
    }
    updateReplyButtonDraftsBadge(num);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    showToast(`✓ Saved reply draft ("${text.slice(0, 24)}…"). Publish via Reply dialog (Shortcut: r).`);
  }

  async function publishPendingDraftsForCl(clNum, drafts) {
    const num = Number(clNum);
    for (const draft of drafts) {
      if (draft.kind === "reply_dialog") continue;
      if (draft.kind === "reply") {
        const resp = await sidecarFetch("/api/gerrit/reply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            cl: num,
            comment_id: draft.comment_id,
            file_path: draft.file_path,
            line: draft.line,
            side: draft.side || "REVISION",
            unresolved: Boolean(draft.unresolved),
            message: draft.message,
          }),
        });
        const data = await resp.json();
        if (!resp.ok || data.error || data.status !== "ok") {
          throw new Error(data.error || `Failed to publish reply on ${draft.file_path}:${draft.line}`);
        }
        const nowUnresolved = Boolean(data.unresolved);
        if (draft.rawItem) {
          applySubmittedReplyToThreadItem(num, draft.rawItem, data, draft.message, nowUnresolved);
        }
        delete state.openReplyByItemId[draft.id];
        const refreshedItem = findInlineItemById(num, draft.id) || draft.rawItem;
        if (refreshedItem) {
          insertOrReplaceInlineItemInDom(num, refreshedItem, { forceRender: true });
        }
      } else if (draft.kind === "draft") {
        const resp = await sidecarFetch("/api/gerrit/comment", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            cl: num,
            file_path: draft.file_path,
            line: draft.line || 1,
            start_line: draft.start_line,
            end_line: draft.end_line || draft.line || 1,
            side: draft.side || "REVISION",
            unresolved: Boolean(draft.unresolved),
            message: draft.message,
          }),
        });
        const data = await resp.json();
        if (!resp.ok || data.error || data.status !== "ok") {
          throw new Error(data.error || `Failed to post draft comment on ${draft.file_path}:${draft.line}`);
        }
        const item = findInlineItemById(num, draft.id);
        if (item) {
          item.kind = "comment";
          item.comment_id = String(data.comment_id || "");
          item.author_name = data.author_name || (state.viewer || "you").split("@")[0];
          item.root_author_name = item.author_name;
          item.patch_set = Number(data.patch_set || 0) || null;
          insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
        }
      }
    }
    invalidateClientQueueCache();
  }

  function syncReplyDialogVisibilityDom() {
    const backdrop = document.getElementById("replyDialogBackdrop");
    if (!backdrop) return;
    const isOpen = Boolean(state.replyDialogOpen);
    backdrop.classList.toggle("open", isOpen);
    backdrop.setAttribute("aria-hidden", isOpen ? "false" : "true");
  }

  function openReplyDialog(opts = {}) {
    if (state.votingInFlight || state.submittingInFlight) return;
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (isBatch || !primaryCl) return;
    if (state.codeSelection && Number(state.codeSelection.clNum) === Number(primaryCl.number)) {
      saveSelectionDraftAsCollapsedItem(state.codeSelection);
    }
    ensureStagedVoteContextSynced();
    restorePgReplyDraftForCl(primaryCl.number);
    state.replyDialogOpen = true;
    if (typeof setPgReplyMessageTab === "function") {
      setPgReplyMessageTab("edit", primaryCl.number);
    }
    syncReplyDialogVisibilityDom();
    syncReplyDraftsSectionDom(primaryCl.number);
    updateReplyButtonDraftsBadge(primaryCl.number);
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    syncPgReplyQuickChipsActiveState();
    syncPgReplyParticipantsDom(primaryCl.number);
    syncPgReplyDraftDiscardButton();
    updatePgReplyDialogAutosaveBadge("saved");
    if (opts.focusComment !== false) {
      const inp = document.getElementById("stagedVoteCommentInput");
      if (inp && typeof inp.focus === "function") inp.focus();
    }
  }

  function closeReplyDialog() {
    if (state.votingInFlight) return;
    flushPgReplyDialogDraftAutosave();
    state.replyDialogOpen = false;
    if (state.activeReviewerComposer && state.activeReviewerComposer.context === "reply") {
      state.activeReviewerComposer = null;
    }
    syncReplyDialogVisibilityDom();
    if (state.selectedClNumber) {
      updateReplyButtonDraftsBadge(state.selectedClNumber);
    }
  }

  function cancelReplyDialog() {
    if (state.votingInFlight) return;
    flushPgReplyDialogDraftAutosave();
    state.replyDialogOpen = false;
    if (state.activeReviewerComposer && state.activeReviewerComposer.context === "reply") {
      state.activeReviewerComposer = null;
    }
    state.stagedVote.cr = null;
    state.stagedVote.v = null;
    state.stagedVote.pv = null;
    state.stagedVote.pr = null;
    state.stagedVote.as = null;
    state.stagedVote.perf = null;
    state.stagedVote.lint = null;
    state.stagedVote.bp = null;
    state.stagedVote.api = null;
    state.stagedVote.oo = null;
    state.stagedVote.rs = null;
    state.stagedVote.cq = null;
    state.stagedVote.osl = null;
    state.stagedVote.trust = null;
    state.stagedVote.extraLabels = {};
    syncReplyDialogVisibilityDom();
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    syncPgReplyQuickChipsActiveState();
    if (state.selectedClNumber) {
      updateReplyButtonDraftsBadge(state.selectedClNumber);
    }
  }

  const PG_KEYBOARD_SHORTCUTS = [
    {
      group: "Navigation & Dashboard",
      shortcuts: [
        { keys: ["j"], description: "Next change in dashboard (or next file in change view)" },
        { keys: ["k"], description: "Previous change in dashboard (or previous file in change view)" },
        { keys: ["o", "Enter"], description: "Open selected change" },
        { keys: ["u"], description: "Toggle sidebar / back to dashboard" },
        { keys: ["/"], description: "Focus search bar" },
        { keys: ["Alt", "/"], description: "Open Gerrit search operators cheatsheet" },
        { keys: ["*"], description: "Star / unstar selected change" },
        { keys: ["x"], description: "Select / check change for Batch Edit (in Dashboard or Relation Chain)" },
        { keys: ["Shift", "Click"], description: "Range select changes for Batch Edit (or drag across rows / checkboxes)" },
        { keys: ["d"], description: "Download patch & checkout commands (d)" },
        { keys: ["1", "2", "3", "4", "5", "6", "7"], description: "Select command tab (Repo, Checkout, Cherry Pick, Format Patch, Pull, Patch Apply, All) in Download dialog" },
        { keys: ["s"], description: "Cycle download scheme (repo/http/sso/ssh/jj) in Download dialog (or Star focused relation item)" },
        { keys: ["[", "]"], description: "Step patchset down/up on focused relation chain item (or in Download dialog)" },
        { keys: ["Shift", "C"], description: "Cherry pick change to another branch" },
        { keys: ["Shift", "U"], description: "Create follow-up change stacked on current CL" },
        { keys: ["Shift", "G"], description: "Inspect branches and tags containing change (Included In)" },
        { keys: ["Shift", "L"], description: "Copy links and references dropdown" },
        { keys: ["Shift", "Q"], description: "Manage attention set (toggle Add Reviewer popover)" },
        { keys: ["Shift", "B"], description: "Inspect attention set reason history log" },
        { keys: ["Alt", "H"], description: "Add hashtag to change in metadata (Alt+H)" },
        { keys: ["Alt", "R"], description: "Cycle related changes tab (Relation chain, Submitted together, Topic, Cherry-picks)" },
        { keys: ["Alt", "U"], description: "Rebase change on parent branch or stack parent (Alt+U)" },
        { keys: ["Alt", "W"], description: "Toggle Work in Progress (WIP) / Ready for review (active) (Alt+W)" },
        { keys: ["Shift", "Z"], description: "Inspect submit requirements & submittability audit" },
        { keys: ["Shift", "S"], description: "Toggle Continuous Stack / Whole Stack review" },
        { keys: ["Shift", "["], description: "Jump to previous commit in stack (Whole Stack mode)" },
        { keys: ["Shift", "]"], description: "Jump to next commit in stack (Whole Stack mode)" },
        { keys: ["Shift", "Enter"], description: "Toggle active stack commit reviewed (Whole Stack mode)" },
        { keys: ["Alt", "T"], description: "Inspect stack submit sequence & checkpoints (Whole Stack mode)" },
        { keys: ["Alt", "F"], description: "Inspect cumulative stack files & jump to diff (Whole Stack mode)" },
        { keys: ["Alt", "I"], description: "Toggle stack interdiff (PS n-1 vs PS n) across all commits in stack (Whole Stack mode)" },
        { keys: ["Alt", "T"], description: "Add or edit topic on change in metadata (Alt+T)" },
        { keys: ["Shift", "K"], description: "Toggle between Files and Checks tab" },
        { keys: ["l"], description: "Toggle Run Log console drawer for selected check run" },
        { keys: ["r"], description: "Rerun selected check run (in Checks tab)" },
        { keys: ["Shift", "Y"], description: "Manage custom dashboard sections & saved searches" },
      ],
    },
    {
      group: "File List & Diff Navigation",
      shortcuts: [
        { keys: ["]"], description: "Next file in change (advances cursor)" },
        { keys: ["["], description: "Previous file in change (advances cursor)" },
        { keys: ["Alt", "]"], description: "Jump to next unreviewed file diff (Alt+])" },
        { keys: ["Alt", "["], description: "Jump to previous unreviewed file diff (Alt+[)" },
        { keys: ["Alt", "J"], description: "Move file list cursor to next unreviewed file (Alt+J)" },
        { keys: ["Alt", "K"], description: "Move file list cursor to previous unreviewed file (Alt+K)" },
        { keys: ["Shift", "Alt", "U"], description: "Jump to first unreviewed file in change (Shift+Alt+U)" },
        { keys: ["Shift", "J"], description: "Jump to file selector / dropdown" },
        { keys: ["n"], description: "Next diff chunk or comment thread" },
        { keys: ["p"], description: "Previous diff chunk or comment thread" },
        { keys: ["Shift", "N"], description: "Next unresolved comment thread" },
        { keys: ["Shift", "P"], description: "Previous unresolved comment thread" },
        { keys: ["Shift", "Alt", "N"], description: "Next code diff chunk (skipping comments) (Shift+Alt+N)" },
        { keys: ["Shift", "Alt", "P"], description: "Previous code diff chunk (skipping comments) (Shift+Alt+P)" },
        { keys: ["Alt", "N"], description: "Next comment thread in change (Alt+N or j when focused)" },
        { keys: ["Alt", "P"], description: "Previous comment thread in change (Alt+P or k when focused)" },
        { keys: ["Shift", "R"], description: "Toggle show / hide resolved comments" },
        { keys: ["Shift", "W"], description: "Toggle show / hide robot comments" },
        { keys: ["Shift", "E"], description: "Expand / collapse all comment threads across change" },
        { keys: ["Shift", "D"], description: "Jump to next draft comment / view pending drafts" },
        { keys: ["f"], description: "Toggle active / focused file reviewed & advance" },
        { keys: ["r"], description: "Toggle reviewed checkbox on focused file (File List)" },
        { keys: ["x"], description: "Toggle reviewed checkbox on focused file (File List)" },
        { keys: ["Shift", "Click"], description: "Mark range of files reviewed or unreviewed" },
        { keys: ["Shift", "J", "/", "K"], description: "Extend selection range in file list" },
        { keys: ["u"], description: "Mark selected range as unreviewed (u)" },
        { keys: ["Escape"], description: "Clear file list range selection (Esc)" },
        { keys: ["h"], description: "Toggle hide / show reviewed files (File List)" },
        { keys: ["Shift", "V"], description: "Toggle mark all files reviewed / unreviewed" },
        { keys: ["Ctrl", "F"], description: "Find text or regex across diff lines (Enter / F3 / n: next, Shift+Enter / Shift+F3 / N: prev)" },
        { keys: ["F3"], description: "Next search match in diff (Shift+F3 for previous match)" },
        { keys: ["Shift", "F"], description: "Focus file list filter input (Shift+F or /)" },
        { keys: ["i"], description: "Toggle active file diff expand / collapse (i)" },
        { keys: ["Shift", "I"], description: "Expand all file diffs in change (Shift+I)" },
        { keys: ["Shift", "E"], description: "Collapse all file diffs in change (Shift+E)" },
        { keys: ["Shift", "X"], description: "Expand all skipped diff context lines" },
        { keys: ["Shift", "Alt", "X"], description: "Toggle expand / restore all context lines in active file (Shift+Alt+X)" },
        { keys: ["j", "/", "k"], description: "Expand context lines below / above on focused context bar (Shift: +50, Alt: +100)" },
        { keys: ["▲ +10", "▲ +25", "▲ +50"], description: "Expand context lines above hunk directly from hunk header bar (or k / Up on focused hunk)" },
        { keys: ["▼ +10", "▼ +25", "▼ +50"], description: "Expand trailing context lines below diff to EOF from trailing context bar (or j / Down on bar)" },
        { keys: ["Shift", "O"], description: "Toggle show only files missing code owner approval" },
        { keys: ["Shift", "H"], description: "Toggle filter files with unresolved comments or drafts" },
        { keys: ["Shift", "T"], description: "Cycle file list sorting (Path, Delta/Size, Comments, Owners, Reviewed)" },
        { keys: ["y"], description: "Toggle file list mode (Tree view / Flat list)" },
        { keys: ["p", "/", "Shift", "P"], description: "Copy focused file path (p) or all file paths (Shift+P) to clipboard in File List" },
        { keys: ["Alt", "Y"], description: "Toggle collapse / expand all directories in tree view (Alt+Y)" },
        { keys: ["j", "/", "k"], description: "Navigate diff lines down / up in diff view (or next/prev file in File List)" },
        { keys: ["Shift", "Down", "/", "Shift", "Up"], description: "Extend line selection range down / up in diff view (Shift+Down/Up or Shift+J/K)" },
        { keys: ["ArrowDown", "ArrowUp"], description: "Navigate file rows in file list (or diff lines in diff view)" },
        { keys: ["Alt", "M"], description: "Jump to Commit Message diff (Alt+M, or m in File List; cycles to Merge List)" },
        { keys: ["m"], description: "Scroll to Change Log & focus message filter" },
        { keys: ["Shift", "M"], description: "Cycle image diff mode (2-up / Swipe / Onion skin)" },
        { keys: ["Space"], description: "Flip image sides / toggle blink comparator (in image diff)" },
        { keys: ["c"], description: "Add new comment on selected lines or focused diff line (c)" },
        { keys: ["Alt", "C"], description: "Cycle diff color scheme (Alt+C)" },
        { keys: ["Shift", "Alt", "C"], description: "Cycle comment author / reviewer filter in Diff & Comments (Shift+Alt+C)" },
        { keys: ["Alt", "D"], description: "Increase diff line spacing / density (Alt+D)" },
        { keys: ["Shift", "Alt", "D"], description: "Decrease diff line spacing / tighter (Shift+Alt+D)" },
        { keys: ["Alt", "+"], description: "Increase diff font size (Alt+= or Alt++)" },
        { keys: ["Alt", "-"], description: "Decrease diff font size (Alt+-)" },
        { keys: ["Alt", "0"], description: "Reset diff font size to default 12px (Alt+0)" },
        { keys: ["Shift", "Alt", "0"], description: "Reset diff line spacing to default 1.50 (Shift+Alt+0)" },
        { keys: ["Alt", "G"], description: "Cycle diff column limit guide (Off, 80, 100, 120) (Alt+G)" },
        { keys: ["Alt", "X"], description: "Cycle diff context lines (10, 25, 50, 100, Whole file) (Alt+X)" },
        { keys: ["Alt", "↑"], description: "Increase diff context lines (Alt+↑)" },
        { keys: ["Alt", "↓"], description: "Decrease diff context lines (Alt+↓)" },
        { keys: ["M"], description: "Mark file as reviewed and jump to next unreviewed file (M)" },
        { keys: ["Alt", "S"], description: "Cycle file list status filter (All, Added, Modified, Deleted, Renamed) (Alt+S)" },
        { keys: ["Alt", "E"], description: "Cycle file list extension / type filter (Alt+E)" },
        { keys: ["Alt", "B"], description: "Cycle file list size / delta tier filter (All, XS, S, M, L, XL) (Alt+B)" },
        { keys: ["v"], description: "Toggle diff view mode (Side-by-side / Unified)" },
        { keys: ["a", "/", "b"], description: "Switch diff side to Base (a / Left) or Revision (b / Right) in side-by-side diff" },
        { keys: ["ArrowLeft", "/", "ArrowRight"], description: "Switch active diff side (Left / Right) in side-by-side diff" },
        { keys: ["w"], description: "Toggle diff word wrap (Wrap lines)" },
        { keys: ["l"], description: "Toggle diff line numbers visibility (l)" },
        { keys: ["Alt", "B"], description: "Toggle blame annotations on active file diff (b / Alt+B)" },
        { keys: ["Alt", "A"], description: "Toggle hide / show left diff pane (Base side) in side-by-side mode (Alt+A or Shift+A)" },
        { keys: ["Ctrl", "G"], description: "Go to line number in active file diff (Ctrl+G or :)" },
        { keys: ["y"], description: "Copy permalink to focused line or selection in diff (y)" },
        { keys: [","], description: "Open diff preferences dialog" },
        { keys: ["Alt", "O"], description: "Toggle symbol outline / quick jump menu in active file diff (Alt+O or Ctrl+Shift+O)" },
        { keys: ["Shift", "Alt", "O"], description: "Toggle diff chunk overview minimap (Shift+Alt+O, or Alt+O in diff)" },
        { keys: ["Shift", "Alt", "T"], description: "Toggle show tabs (visual tab markers) (Shift+Alt+T)" },
        { keys: ["Shift", "Alt", "W"], description: "Toggle show whitespace errors (trailing whitespace) (Shift+Alt+W)" },
        { keys: ["Shift", "Alt", "I"], description: "Toggle intraline difference highlighting (Shift+Alt+I)" },
        { keys: ["Shift", "Up"], description: "Previous patchset revision" },
        { keys: ["Shift", "Down"], description: "Next patchset revision" },
        { keys: ["b"], description: "Toggle diff comparison (Base vs Previous patchset) · Toggle blame annotations in diff view (b)" },
        { keys: ["z"], description: "Expand / collapse commit message (or collapse all messages in Change Log) · Cycle image zoom when viewing image diff (z)" },
        { keys: ["Alt", "Z"], description: "Cycle image diff zoom (Fit, 100%, 200%, 400%) (Alt+Z)" },
        { keys: ["e"], description: "Collapse active file diff / Edit active file in browser (e)" },
        { keys: ["Alt", "1"], description: "Switch code composer to Review Comment mode (Alt+1)" },
        { keys: ["Alt", "2"], description: "Switch code composer to Ask Agent mode (Alt+2)" },
        { keys: ["Alt", "M"], description: "Toggle between Comment and Ask Agent modes in code composer (Alt+M or Tab)" },
        { keys: ["Tab"], description: "Toggle between Comment and Ask Agent tabs in code composer (Tab / Shift+Tab)" },
        { keys: ["Click"], description: "Click line number gutter or '+' to draft inline comment (Alt+Click to Ask Agent)" },
        { keys: ["Drag"], description: "Drag across gutter line numbers to select multi-line range (or across file rows / checkboxes to select files)" },
      ],
    },
    {
      group: "Reviewing & Voting",
      shortcuts: [
        { keys: ["a"], description: "Open Reply dialog with Code-Review +2 staged" },
        { keys: ["r"], description: "Reply to focused comment thread / Open Reply dialog with comment input focused (r)" },
        { keys: ["d"], description: "Quick reply 'Done' and resolve thread (when comment is focused)" },
        { keys: ["q"], description: "Quote focused change message or comment thread in reply (q)" },
        { keys: ["Ctrl", "Enter"], description: "Save draft comment (in draft editor or code selection composer)" },
        { keys: ["Ctrl", "Shift", "P"], description: "Cycle commit message editor mode (Write / Preview / Split)" },
        { keys: ["1"], description: "Stage Code-Review +1" },
        { keys: ["2"], description: "Stage Code-Review +2" },
        { keys: ["0"], description: "Clear Code-Review vote (0)" },
        { keys: ["Shift", "P"], description: "Stage Presubmit-Ready (PR +1)" },
        { keys: ["Shift", "A"], description: "Stage CR +2 & Autosubmit +1" },
        { keys: ["Alt", "V"], description: "Toggle / stage Verified +1 (Alt+V)" },
        { keys: ["Alt", "L"], description: "Apply quick LGTM +2 preset in Reply dialog (Alt+L)" },
        { keys: ["Ctrl", "B"], description: "Bold text in reply message (**text**)" },
        { keys: ["Ctrl", "I"], description: "Italic text in reply message (_text_)" },
        { keys: ["Ctrl", "E"], description: "Inline code in reply message (`code`)" },
        { keys: ["Ctrl", "K"], description: "Insert link in reply message ([text](url))" },
        { keys: ["Alt", "P"], description: "Toggle Edit / Live Preview mode in reply dialog (Alt+P)" },
        { keys: ["Enter"], description: "Submit staged votes" },
      ],
    },
    {
      group: "AI Agent & Tools",
      shortcuts: [
        { keys: ["s"], description: "Trigger Review Swarm AI agent" },
        { keys: ["t"], description: "Toggle PolyGerrit Dark / Light theme" },
        { keys: ["?"], description: "Show / hide keyboard shortcuts cheatsheet" },
        { keys: ["Esc"], description: "Close open dialog or clear active selection" },
      ],
    },
  ];

  function highlightPgShortcutText(text, query) {
    const str = String(text || "");
    const q = String(query || "").trim();
    if (!q) return escapeHtml(str);
    const escapedQ = escapeRegex(q);
    const regex = new RegExp(`(${escapedQ})`, "gi");
    const parts = str.split(regex);
    return parts.map(part => {
      if (part.toLowerCase() === q.toLowerCase()) {
        return `<mark class="pg-shortcut-highlight">${escapeHtml(part)}</mark>`;
      }
      return escapeHtml(part);
    }).join("");
  }

  function getPgShortcutsCategoryCounts(filterText = "") {
    const q = String(filterText || "").trim().toLowerCase();
    let totalAll = 0;
    let matchedAll = 0;
    const groups = PG_KEYBOARD_SHORTCUTS.map(g => {
      const inGroup = q ? g.group.toLowerCase().includes(q) : false;
      const totalInGroup = g.shortcuts.length;
      totalAll += totalInGroup;
      const matchedCount = g.shortcuts.filter(s => {
        if (!q || inGroup) return true;
        const inDesc = s.description.toLowerCase().includes(q);
        const inKeys = s.keys.some(k => k.toLowerCase().includes(q));
        return inDesc || inKeys;
      }).length;
      matchedAll += matchedCount;
      return {
        group: g.group,
        total: totalInGroup,
        matched: matchedCount,
      };
    });
    return {
      totalAll,
      matchedAll,
      groups,
    };
  }

  function renderPgShortcutsCategoryBarHtml(filterText = "", activeCategory = "") {
    const counts = getPgShortcutsCategoryCounts(filterText);
    const currentCat = activeCategory || state.shortcutsActiveCategory || "all";
    const isAllActive = currentCat === "all" || !currentCat;

    const allCountBadge = filterText
      ? `<span class="pg-shortcuts-cat-badge">${counts.matchedAll}/${counts.totalAll}</span>`
      : `<span class="pg-shortcuts-cat-badge">${counts.totalAll}</span>`;

    const allChip = `<button type="button" class="pg-shortcuts-cat-chip ${isAllActive ? "is-active" : ""}" role="tab" aria-selected="${isAllActive ? "true" : "false"}" onclick="selectPgShortcutsCategory('all')" title="Show all shortcut categories">All ${allCountBadge}</button>`;

    const groupChips = counts.groups.map(g => {
      const isActive = currentCat === g.group;
      const isDisabled = Boolean(filterText && g.matched === 0);
      const countBadge = filterText
        ? `<span class="pg-shortcuts-cat-badge">${g.matched}/${g.total}</span>`
        : `<span class="pg-shortcuts-cat-badge">${g.total}</span>`;
      const disabledAttr = isDisabled ? ' disabled aria-disabled="true"' : "";
      const disabledCls = isDisabled ? " is-disabled" : "";
      return `<button type="button" class="pg-shortcuts-cat-chip ${isActive ? "is-active" : ""}${disabledCls}" role="tab" aria-selected="${isActive ? "true" : "false"}" onclick="selectPgShortcutsCategory('${escapeHtml(g.group)}')"[disabledAttr] title="Filter to ${escapeHtml(g.group)} shortcuts">${escapeHtml(g.group)} ${countBadge}</button>`.replace("[disabledAttr]", disabledAttr);
    }).join("");

    return `${allChip}${groupChips}`;
  }

  function updatePgShortcutsMatchCount(totalMatched, totalAll, filterText = "") {
    const countEl = document.getElementById("pgShortcutsMatchCount");
    if (!countEl) return;
    if (!filterText) {
      countEl.textContent = `${totalAll} shortcuts`;
      countEl.title = `Total available keyboard shortcuts: ${totalAll}`;
    } else {
      countEl.textContent = `${totalMatched} of ${totalAll} shortcuts`;
      countEl.title = `Showing ${totalMatched} matching shortcuts out of ${totalAll}`;
    }
  }

  function selectPgShortcutsCategory(cat) {
    if (state.shortcutsActiveCategory === cat && cat !== "all") {
      state.shortcutsActiveCategory = "all";
    } else {
      state.shortcutsActiveCategory = cat;
    }
    const searchInp = document.getElementById("pgShortcutsSearchInput");
    const q = searchInp ? searchInp.value : "";
    syncPgShortcutsDialogView(q);
  }

  function syncPgShortcutsDialogView(filterText = "") {
    const q = String(filterText || "").trim();
    const catBar = document.getElementById("pgShortcutsCategoryBar");
    if (catBar) {
      catBar.innerHTML = renderPgShortcutsCategoryBarHtml(q, state.shortcutsActiveCategory);
    }
    const content = document.getElementById("pgShortcutsContent");
    if (content) {
      content.innerHTML = renderPgShortcutsHtml(q, state.shortcutsActiveCategory);
    }
    state.shortcutsFocusedIndex = -1;
  }

  function navigatePgShortcutsMatches(dir) {
    const rows = Array.from(document.querySelectorAll("#pgShortcutsContent .pg-shortcut-row"));
    if (rows.length === 0) return;
    let nextIdx = (typeof state.shortcutsFocusedIndex === "number" ? state.shortcutsFocusedIndex : -1) + dir;
    if (nextIdx < 0) {
      state.shortcutsFocusedIndex = -1;
      rows.forEach(r => r.classList.remove("is-active-match"));
      const inp = document.getElementById("pgShortcutsSearchInput");
      if (inp) inp.focus();
      return;
    }
    if (nextIdx >= rows.length) {
      nextIdx = 0;
    }
    state.shortcutsFocusedIndex = nextIdx;
    rows.forEach((r, idx) => {
      if (idx === nextIdx) {
        r.classList.add("is-active-match");
        r.focus();
        if (typeof r.scrollIntoView === "function") {
          r.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      } else {
        r.classList.remove("is-active-match");
      }
    });
  }

  function onShortcutsContentKeydown(e) {
    if (e.key === "ArrowDown" || e.key === "Down") {
      e.preventDefault();
      navigatePgShortcutsMatches(1);
    } else if (e.key === "ArrowUp" || e.key === "Up") {
      e.preventDefault();
      navigatePgShortcutsMatches(-1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      clearShortcutsSearch();
    }
  }

  function renderPgShortcutsHtml(filterText = "", activeCategory = "") {
    const q = String(filterText || "").trim().toLowerCase();
    const currentCat = activeCategory || state.shortcutsActiveCategory || "all";
    let totalMatched = 0;
    let totalAll = 0;

    const groupsHtml = PG_KEYBOARD_SHORTCUTS.map((g, grpIdx) => {
      totalAll += g.shortcuts.length;
      if (currentCat !== "all" && currentCat !== g.group) {
        return "";
      }
      const inGroup = q ? g.group.toLowerCase().includes(q) : false;
      const matchingShortcuts = g.shortcuts.filter(s => {
        if (!q || inGroup) return true;
        const inDesc = s.description.toLowerCase().includes(q);
        const inKeys = s.keys.some(k => k.toLowerCase().includes(q));
        return inDesc || inKeys;
      });
      if (matchingShortcuts.length === 0) return "";
      totalMatched += matchingShortcuts.length;

      const rows = matchingShortcuts.map((s, sIdx) => {
        const keysHtml = s.keys.map(k => `<kbd class="pg-kbd">${highlightPgShortcutText(k, q)}</kbd>`).join('<span class="pg-kbd-separator">+</span>');
        const descHtml = highlightPgShortcutText(s.description, q);
        const rowId = `pgShortcutRow-${grpIdx}-${sIdx}`;
        return `<div class="pg-shortcut-row" id="${rowId}" data-keys="${escapeHtml(s.keys.join('+'))}" tabindex="0">
          <div class="pg-shortcut-keys">${keysHtml}</div>
          <div class="pg-shortcut-desc">${descHtml}</div>
        </div>`;
      }).join("");

      const highlightedGroup = highlightPgShortcutText(g.group, q);
      const badgeText = q ? `${matchingShortcuts.length} / ${g.shortcuts.length}` : `${g.shortcuts.length}`;
      const badgeTitle = `${matchingShortcuts.length} shortcut${matchingShortcuts.length === 1 ? "" : "s"} in this category`;

      return `<div class="pg-shortcut-group">
        <div class="pg-shortcut-group-header">
          <h3 class="pg-shortcut-group-title">${highlightedGroup}</h3>
          <span class="pg-shortcut-group-badge" title="${badgeTitle}">${badgeText}</span>
        </div>
        <div class="pg-shortcut-list">${rows}</div>
      </div>`;
    }).filter(Boolean).join("");

    updatePgShortcutsMatchCount(totalMatched, totalAll, q);

    if (totalMatched === 0) {
      const catNotice = currentCat !== "all" ? ` in category "<strong>${escapeHtml(currentCat)}</strong>"` : "";
      return `<div class="pg-shortcuts-empty">
        <span class="pg-shortcuts-empty-icon" aria-hidden="true">🔍</span>
        <div>No keyboard shortcuts matching "<strong>${escapeHtml(filterText)}</strong>"${catNotice}</div>
        <button type="button" class="pg-btn-flat" onclick="clearShortcutsSearch()">Clear Filter</button>
      </div>`;
    }
    return `<div class="pg-shortcuts-grid">${groupsHtml}</div>`;
  }

  function openShortcutsDialog() {
    state.shortcutsDialogOpen = true;
    state.shortcutsActiveCategory = "all";
    state.shortcutsFocusedIndex = -1;
    const backdrop = document.getElementById("pgShortcutsBackdrop");
    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    const searchInp = document.getElementById("pgShortcutsSearchInput");
    const q = searchInp ? searchInp.value : "";
    syncPgShortcutsDialogView(q);
    setTimeout(() => {
      const inp = document.getElementById("pgShortcutsSearchInput");
      if (inp) {
        inp.focus();
        inp.select();
      }
    }, 50);
  }

  function closeShortcutsDialog() {
    state.shortcutsDialogOpen = false;
    state.shortcutsFocusedIndex = -1;
    const backdrop = document.getElementById("pgShortcutsBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function toggleShortcutsDialog() {
    if (state.shortcutsDialogOpen) {
      closeShortcutsDialog();
    } else {
      openShortcutsDialog();
    }
  }

  function onShortcutsBackdropClick(e) {
    if (e.target && e.target.id === "pgShortcutsBackdrop") {
      closeShortcutsDialog();
    }
  }

  function onShortcutsSearchInput(val) {
    const clearBtn = document.getElementById("pgShortcutsClearBtn");
    if (clearBtn) {
      clearBtn.style.display = val ? "inline-flex" : "none";
    }
    syncPgShortcutsDialogView(val);
  }

  function clearShortcutsSearch() {
    const inp = document.getElementById("pgShortcutsSearchInput");
    if (inp) {
      inp.value = "";
      inp.focus();
    }
    state.shortcutsActiveCategory = "all";
    state.shortcutsFocusedIndex = -1;
    const clearBtn = document.getElementById("pgShortcutsClearBtn");
    if (clearBtn) {
      clearBtn.style.display = "none";
    }
    syncPgShortcutsDialogView("");
  }

  function onShortcutsSearchKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      if (e.target.value) {
        clearShortcutsSearch();
      } else {
        closeShortcutsDialog();
      }
      return;
    }
    if (e.key === "ArrowDown" || e.key === "Down") {
      e.preventDefault();
      navigatePgShortcutsMatches(1);
      return;
    }
    if (e.key === "ArrowUp" || e.key === "Up") {
      e.preventDefault();
      navigatePgShortcutsMatches(-1);
      return;
    }
  }

  function openSelectedCl() {
    if (!state.selectedClNumber) {
      const orderedItems = getOrderedVisibleCommits(getVisibleStacks());
      if (orderedItems.length > 0) {
        selectCl(orderedItems[0].commit.number);
      }
      return;
    }
    if (state.sidebarCollapsed) {
      toggleSidebarCollapsed(false);
    }
    const diffEl = document.getElementById("diffScroll") || document.getElementById("diffPane");
    if (diffEl) {
      diffEl.scrollTop = 0;
      if (typeof diffEl.focus === "function") diffEl.focus();
    }
  }

