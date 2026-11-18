  function setStagedPrValue(targetVal) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (isBatch) {
      toggleStagedPr(Number(targetVal) >= 1);
      return;
    }
    if (!primaryCl) return;
    const desired = Number(targetVal) >= 1 ? 1 : 0;
    const curPr = getEffectiveCurrentViewerPr(primaryCl);
    state.stagedVote.pr = (state.stagedVote.pr === desired || desired === curPr) ? null : desired;
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  const PG_REPLY_QUICK_PRESETS = [
    { id: "lgtm_cr2", icon: "🚀", label: "LGTM +2", text: "LGTM", cr: 2, desc: "Insert 'LGTM' & stage Code-Review +2 (Shortcut: Alt+L)" },
    { id: "lgtm", icon: "👍", label: "LGTM", text: "LGTM", cr: null, desc: "Insert 'LGTM' into review comment" },
    { id: "ack", icon: "💬", label: "Ack", text: "Acknowledged.", cr: null, desc: "Insert 'Acknowledged.'" },
    { id: "done", icon: "✓", label: "Done", text: "Done.", cr: null, desc: "Insert 'Done.'" },
    { id: "addressed", icon: "⚡", label: "Addressed", text: "Addressed review comments, please take another look.", cr: null, desc: "Insert 'Addressed review comments, please take another look.'" },
    { id: "rebase", icon: "🔄", label: "Please rebase", text: "Please rebase onto upstream and re-test.", cr: null, desc: "Insert 'Please rebase onto upstream and re-test.'" },
    { id: "waiting_ci", icon: "⏳", label: "Waiting CI", text: "Waiting for presubmit verification results before approving.", cr: null, desc: "Insert 'Waiting for presubmit verification results before approving.'" },
  ];

  function renderPgReplyQuickChipsHtml(c = null) {
    const curComment = (state.stagedVote && state.stagedVote.comment) || "";
    const curCr = state.stagedVote && state.stagedVote.cr;
    return PG_REPLY_QUICK_PRESETS.map(p => {
      const isCrMatch = p.cr !== null ? (curCr === p.cr) : true;
      const isTextMatch = p.text ? curComment.includes(p.text) : false;
      const isActive = (p.cr !== null && isCrMatch && isTextMatch) || (p.cr === null && isTextMatch);
      const activeCls = isActive ? " is-active" : "";
      return `<button
        type="button"
        id="btnReplyQuickPreset-${p.id}"
        class="pg-reply-quick-chip${activeCls}"
        data-preset="${p.id}"
        onclick="applyPgReplyQuickPreset('${p.id}')"
        title="${escapeHtml(p.desc)}"
      ><span class="pg-reply-quick-icon" aria-hidden="true">${p.icon}</span> <span>${escapeHtml(p.label)}</span></button>`;
    }).join("");
  }

  function applyPgReplyQuickPreset(presetId, showNotification = true) {
    const preset = PG_REPLY_QUICK_PRESETS.find(p => p.id === presetId);
    if (!preset) return "";
    ensureStagedVoteContextSynced();
    const inp = document.getElementById("stagedVoteCommentInput");
    const curVal = (inp && inp.value !== undefined) ? inp.value : (state.stagedVote.comment || "");
    let newVal = curVal;

    if (preset.text) {
      if (!curVal.trim()) {
        newVal = preset.text;
      } else if (curVal.trim() === preset.text.trim()) {
        newVal = "";
      } else if (!curVal.includes(preset.text)) {
        newVal = `${curVal.trim()}\n\n${preset.text}`;
      }
      if (inp) {
        inp.value = newVal;
        try {
          inp.focus();
          const len = inp.value.length;
          inp.setSelectionRange(len, len);
        } catch (_) {}
      }
      onStagedVoteCommentInput(newVal);
    }

    if (preset.cr !== null && typeof toggleStagedCr === "function") {
      toggleStagedCr(preset.cr);
    }

    syncPgReplyQuickChipsActiveState();
    syncActiveActionButtons(getActiveCommitsForDiff(), false);

    if (typeof getPgReplyMessageTab === "function" && getPgReplyMessageTab() === "preview" && typeof renderPgReplyMessageLivePreview === "function") {
      renderPgReplyMessageLivePreview();
    }

    if (showNotification && typeof showToast === "function") {
      showToast(`Applied quick reply "${preset.label}" ✓`);
    }
    return newVal;
  }

  function syncPgReplyQuickChipsActiveState() {
    if (typeof document === "undefined") return;
    const curComment = (state.stagedVote && state.stagedVote.comment) || "";
    const curCr = state.stagedVote && state.stagedVote.cr;
    PG_REPLY_QUICK_PRESETS.forEach(p => {
      const btn = document.getElementById(`btnReplyQuickPreset-${p.id}`);
      if (!btn) return;
      const isCrMatch = p.cr !== null ? (curCr === p.cr) : true;
      const isTextMatch = p.text ? curComment.includes(p.text) : false;
      const isActive = (p.cr !== null && isCrMatch && isTextMatch) || (p.cr === null && isTextMatch);
      if (btn.classList) {
        btn.classList.toggle("is-active", isActive);
      }
      if (typeof btn.setAttribute === "function") {
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      }
    });
  }

  function clearStagedVoteFieldsAfterSend(clNumOverride = null) {
    const num = Number(clNumOverride || state.selectedClNumber || 0);
    if (num) {
      deletePgReplyDraft(num);
    }
    deletePgReplyDraft("batch");
    if (state.replyDialogAutosaveTimer) {
      clearTimeout(state.replyDialogAutosaveTimer);
      state.replyDialogAutosaveTimer = null;
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
    state.stagedVote.comment = "";
    state.stagedVote.noteOpen = false;
    state.replyDialogOpen = false;
    if (typeof setPgReplyMessageTab === "function") {
      setPgReplyMessageTab("edit", num);
    }
    const noteInp = document.getElementById("stagedVoteCommentInput");
    if (noteInp) noteInp.value = "";
    updatePgReplyDialogAutosaveBadge("saved");
    syncPgReplyDraftDiscardButton();
    syncReplyDialogVisibilityDom();
    syncPgReplyQuickChipsActiveState();
    if (num) {
      updateReplyButtonDraftsBadge(num);
      syncReplyDraftsSectionDom(num);
    }
  }

  function resetStagedVote() {
    if (state.votingInFlight) return;
    clearStagedVoteFieldsAfterSend();
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedCr(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.cr = state.stagedVote.cr === targetVal ? null : targetVal;
    } else {
      const curCr = Number(primaryCl.viewer_cr_vote || (primaryCl.approved_by_viewer ? 2 : 0));
      if (state.stagedVote.cr === targetVal || targetVal === curCr) {
        state.stagedVote.cr = null;
      } else {
        state.stagedVote.cr = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    syncPgReplyQuickChipsActiveState();
  }

  function toggleStagedVerified(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.v = state.stagedVote.v === targetVal ? null : targetVal;
    } else {
      const curV = Number(primaryCl.viewer_verified_vote || 0);
      if (state.stagedVote.v === targetVal || targetVal === curV) {
        state.stagedVote.v = null;
      } else {
        state.stagedVote.v = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedPresubmitVerified(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.pv = state.stagedVote.pv === targetVal ? null : targetVal;
    } else {
      const curPv = Number(primaryCl.viewer_presubmit_verified_vote || 0);
      if (state.stagedVote.pv === targetVal || targetVal === curPv) {
        state.stagedVote.pv = null;
      } else {
        state.stagedVote.pv = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedPr(explicitChecked = null) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    if (isBatch) {
      const nextOn = typeof explicitChecked === "boolean" ? explicitChecked : (state.stagedVote.pr !== 1);
      state.stagedVote.pr = nextOn ? 1 : null;
    } else {
      const curPr = getEffectiveCurrentViewerPr(primaryCl);
      const effectiveBefore = state.stagedVote.pr !== null ? state.stagedVote.pr : curPr;
      const nextVal = typeof explicitChecked === "boolean" ? (explicitChecked ? 1 : 0) : (effectiveBefore >= 1 ? 0 : 1);
      state.stagedVote.pr = nextVal === curPr ? null : nextVal;
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedAs(explicitChecked = null) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    if (isBatch) {
      const nextOn = typeof explicitChecked === "boolean" ? explicitChecked : (state.stagedVote.as !== 1);
      state.stagedVote.as = nextOn ? 1 : null;
    } else {
      const curAs = primaryCl.viewer_autosubmit ? 1 : 0;
      const effectiveBefore = state.stagedVote.as !== null ? state.stagedVote.as : curAs;
      const nextVal = typeof explicitChecked === "boolean" ? (explicitChecked ? 1 : 0) : (effectiveBefore >= 1 ? 0 : 1);
      state.stagedVote.as = nextVal === curAs ? null : nextVal;
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedPerformance(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.perf = state.stagedVote.perf === targetVal ? null : targetVal;
    } else {
      const curPerf = Number(primaryCl.viewer_performance_vote || 0);
      if (state.stagedVote.perf === targetVal || targetVal === curPerf) {
        state.stagedVote.perf = null;
      } else {
        state.stagedVote.perf = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedLint(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.lint = state.stagedVote.lint === targetVal ? null : targetVal;
    } else {
      const curLint = Number(primaryCl.viewer_lint_vote || 0);
      if (state.stagedVote.lint === targetVal || targetVal === curLint) {
        state.stagedVote.lint = null;
      } else {
        state.stagedVote.lint = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedBypassPresubmit(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.bp = state.stagedVote.bp === targetVal ? null : targetVal;
    } else {
      const curBp = Number(primaryCl.viewer_bypass_presubmit_vote || 0);
      if (state.stagedVote.bp === targetVal || targetVal === curBp) {
        state.stagedVote.bp = null;
      } else {
        state.stagedVote.bp = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedApiReview(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.api = state.stagedVote.api === targetVal ? null : targetVal;
    } else {
      const curApi = Number(primaryCl.viewer_api_review_vote || 0);
      if (state.stagedVote.api === targetVal || targetVal === curApi) {
        state.stagedVote.api = null;
      } else {
        state.stagedVote.api = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedOwnersOverride(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.oo = state.stagedVote.oo === targetVal ? null : targetVal;
    } else {
      const curOo = Number(primaryCl.viewer_owners_override_vote || 0);
      if (state.stagedVote.oo === targetVal || targetVal === curOo) {
        state.stagedVote.oo = null;
      } else {
        state.stagedVote.oo = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedRubberStamp(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.rs = state.stagedVote.rs === targetVal ? null : targetVal;
    } else {
      const curRs = Number(primaryCl.viewer_rubber_stamp_vote || 0);
      if (state.stagedVote.rs === targetVal || targetVal === curRs) {
        state.stagedVote.rs = null;
      } else {
        state.stagedVote.rs = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedCommitQueue(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.cq = state.stagedVote.cq === targetVal ? null : targetVal;
    } else {
      const curCq = Number(primaryCl.viewer_commit_queue_vote || 0);
      if (state.stagedVote.cq === targetVal || targetVal === curCq) {
        state.stagedVote.cq = null;
      } else {
        state.stagedVote.cq = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedOpenSourceLicensing(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.osl = state.stagedVote.osl === targetVal ? null : targetVal;
    } else {
      const curOsl = Number(primaryCl.viewer_open_source_licensing_vote || 0);
      if (state.stagedVote.osl === targetVal || targetVal === curOsl) {
        state.stagedVote.osl = null;
      } else {
        state.stagedVote.osl = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedTrust(val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    if (isBatch) {
      state.stagedVote.trust = state.stagedVote.trust === targetVal ? null : targetVal;
    } else {
      const curTrust = Number(primaryCl.viewer_trust_vote || 0);
      if (state.stagedVote.trust === targetVal || targetVal === curTrust) {
        state.stagedVote.trust = null;
      } else {
        state.stagedVote.trust = targetVal;
      }
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleStagedCustomLabel(labelName, val) {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    const { isBatch, primaryCl } = resolveVoteTargets();
    if (!isBatch && !primaryCl) return;
    const targetVal = Number(val);
    state.stagedVote.extraLabels = state.stagedVote.extraLabels || {};
    if (state.stagedVote.extraLabels[labelName] === targetVal) {
      delete state.stagedVote.extraLabels[labelName];
    } else {
      state.stagedVote.extraLabels[labelName] = targetVal;
    }
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
  }

  function toggleExtendedVoteControls() {
    const extRow = document.getElementById("stagedVoteExtendedRow");
    const btn = document.getElementById("btnToggleExtendedVoteLabels");
    if (!extRow) return;
    const isHidden = extRow.style.display === "none";
    extRow.style.display = isHidden ? "flex" : "none";
    if (btn) {
      btn.textContent = isHidden ? "− Fewer labels ▴" : "+ More labels ▾";
    }
  }

  function renderPgReplyMarkdownToolbarHtml(clNum = null) {
    const numAttr = clNum ? ` data-cl="${escapeHtml(String(clNum))}"` : "";
    return `<div class="pg-reply-md-toolbar" id="pgReplyMarkdownToolbar"${numAttr} role="toolbar" aria-label="Markdown formatting toolbar">
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('bold', '${escapeHtml(String(clNum || ""))}')" title="Bold: **text** (Ctrl+B)" aria-label="Bold"><strong aria-hidden="true">B</strong></button>
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('italic', '${escapeHtml(String(clNum || ""))}')" title="Italic: _text_ (Ctrl+I)" aria-label="Italic"><em aria-hidden="true">I</em></button>
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('code', '${escapeHtml(String(clNum || ""))}')" title="Inline code: \`code\` (Ctrl+E)" aria-label="Inline code"><code aria-hidden="true">&lt;/&gt;</code></button>
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('codeblock', '${escapeHtml(String(clNum || ""))}')" title="Code block: \`\`\` (code block)" aria-label="Code block"><span class="pg-fmt-icon" aria-hidden="true">📋</span></button>
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('link', '${escapeHtml(String(clNum || ""))}')" title="Insert link: [text](url) (Ctrl+K)" aria-label="Insert link"><span class="pg-fmt-icon" aria-hidden="true">🔗</span></button>
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('quote', '${escapeHtml(String(clNum || ""))}')" title="Quote lines: &gt; text" aria-label="Quote lines"><span class="pg-fmt-icon" aria-hidden="true">❝</span></button>
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('bullet', '${escapeHtml(String(clNum || ""))}')" title="Bulleted list: - item" aria-label="Bulleted list"><span class="pg-fmt-icon" aria-hidden="true">• List</span></button>
      <button type="button" class="pg-reply-fmt-btn" onclick="formatPgReplyCommentMarkdown('numbered', '${escapeHtml(String(clNum || ""))}')" title="Numbered list: 1. item" aria-label="Numbered list"><span class="pg-fmt-icon" aria-hidden="true">1. List</span></button>
    </div>`;
  }

  function formatPgReplyCommentMarkdown(action, clNum = null) {
    const ta = (typeof document !== "undefined") ? document.getElementById("stagedVoteCommentInput") : null;
    if (!ta) return "";
    const start = (typeof ta.selectionStart === "number") ? ta.selectionStart : 0;
    const end = (typeof ta.selectionEnd === "number") ? ta.selectionEnd : start;
    const val = String(ta.value || "");
    const selectedText = val.substring(start, end);

    let replacement = "";
    let newCursorStart = start;
    let newCursorEnd = end;

    switch (action) {
      case "bold": {
        if (selectedText.startsWith("**") && selectedText.endsWith("**") && selectedText.length >= 4) {
          replacement = selectedText.slice(2, -2);
          newCursorStart = start;
          newCursorEnd = start + replacement.length;
        } else {
          replacement = `**${selectedText || "bold text"}**`;
          newCursorStart = selectedText ? start : start + 2;
          newCursorEnd = selectedText ? start + replacement.length : start + 2 + 9;
        }
        break;
      }
      case "italic": {
        if (selectedText.startsWith("_") && selectedText.endsWith("_") && selectedText.length >= 2) {
          replacement = selectedText.slice(1, -1);
          newCursorStart = start;
          newCursorEnd = start + replacement.length;
        } else {
          replacement = `_${selectedText || "italic text"}_`;
          newCursorStart = selectedText ? start : start + 1;
          newCursorEnd = selectedText ? start + replacement.length : start + 1 + 11;
        }
        break;
      }
      case "code": {
        if (selectedText.startsWith("`") && selectedText.endsWith("`") && selectedText.length >= 2) {
          replacement = selectedText.slice(1, -1);
          newCursorStart = start;
          newCursorEnd = start + replacement.length;
        } else {
          replacement = `\`${selectedText || "code"}\``;
          newCursorStart = selectedText ? start : start + 1;
          newCursorEnd = selectedText ? start + replacement.length : start + 1 + 4;
        }
        break;
      }
      case "codeblock": {
        replacement = `\`\`\`\n${selectedText || "// code snippet"}\n\`\`\``;
        newCursorStart = selectedText ? start + 4 : start + 4;
        newCursorEnd = selectedText ? start + 4 + selectedText.length : start + 4 + 15;
        break;
      }
      case "link": {
        const linkText = selectedText || "link text";
        replacement = `[${linkText}](https://...)`;
        newCursorStart = selectedText ? start + linkText.length + 3 : start + 1;
        newCursorEnd = selectedText ? start + replacement.length - 1 : start + 1 + 9;
        break;
      }
      case "quote": {
        const textToQuote = selectedText || "quoted text";
        const quoted = textToQuote.split("\n").map(ln => (ln.startsWith("> ") ? ln.slice(2) : `> ${ln}`)).join("\n");
        replacement = quoted;
        newCursorStart = start;
        newCursorEnd = start + replacement.length;
        break;
      }
      case "bullet": {
        const textToList = selectedText || "list item";
        const listed = textToList.split("\n").map(ln => (ln.startsWith("- ") ? ln.slice(2) : `- ${ln}`)).join("\n");
        replacement = listed;
        newCursorStart = start;
        newCursorEnd = start + replacement.length;
        break;
      }
      case "numbered": {
        const textToList = selectedText || "list item";
        const listed = textToList.split("\n").map((ln, idx) => {
          const stripped = ln.replace(/^\d+\.\s*/, "");
          return `${idx + 1}. ${stripped}`;
        }).join("\n");
        replacement = listed;
        newCursorStart = start;
        newCursorEnd = start + replacement.length;
        break;
      }
      default:
        return val;
    }

    const updatedVal = val.substring(0, start) + replacement + val.substring(end);
    ta.value = updatedVal;
    if (typeof onStagedVoteCommentInput === "function") {
      onStagedVoteCommentInput(updatedVal);
    }
    try {
      if (typeof ta.focus === "function") ta.focus();
      if (typeof ta.setSelectionRange === "function") {
        ta.setSelectionRange(newCursorStart, newCursorEnd);
      }
    } catch (_) {}

    if (typeof getPgReplyMessageTab === "function" && getPgReplyMessageTab() === "preview" && typeof renderPgReplyMessageLivePreview === "function") {
      renderPgReplyMessageLivePreview();
    }
    return updatedVal;
  }

  function setPgReplyMessageTab(tab = "edit", clNum = null) {
    state.replyMessageTab = (tab === "preview") ? "preview" : "edit";
    if (typeof document === "undefined") return state.replyMessageTab;
    const editTabBtn = document.getElementById("btnReplyTabEdit");
    const previewTabBtn = document.getElementById("btnReplyTabPreview");
    const ta = document.getElementById("stagedVoteCommentInput");
    const previewEl = document.getElementById("pgReplyMessagePreview");
    const toolbar = document.getElementById("pgReplyMarkdownToolbar");

    const isEdit = state.replyMessageTab === "edit";
    if (editTabBtn) {
      if (editTabBtn.classList) editTabBtn.classList.toggle("is-active", isEdit);
      if (typeof editTabBtn.setAttribute === "function") editTabBtn.setAttribute("aria-selected", isEdit ? "true" : "false");
    }
    if (previewTabBtn) {
      if (previewTabBtn.classList) previewTabBtn.classList.toggle("is-active", !isEdit);
      if (typeof previewTabBtn.setAttribute === "function") previewTabBtn.setAttribute("aria-selected", !isEdit ? "true" : "false");
    }
    if (toolbar) {
      toolbar.style.display = isEdit ? "flex" : "none";
    }
    if (ta) {
      ta.style.display = isEdit ? "block" : "none";
    }
    if (previewEl) {
      previewEl.style.display = !isEdit ? "block" : "none";
      if (!isEdit) {
        renderPgReplyMessageLivePreview();
      }
    }
    if (isEdit && ta && typeof ta.focus === "function") {
      try {
        ta.focus();
      } catch (_) {}
    }
    return state.replyMessageTab;
  }

  function getPgReplyMessageTab() {
    return state.replyMessageTab || "edit";
  }

  function togglePgReplyMessageTab(clNum = null) {
    const next = getPgReplyMessageTab() === "edit" ? "preview" : "edit";
    return setPgReplyMessageTab(next, clNum);
  }

  function renderPgReplyMessageMarkdownHtml(rawText) {
    const text = String(rawText || "").trim();
    if (!text) {
      return `<div class="pg-reply-preview-empty"><em>(No review message entered yet — switch to Edit tab or select a quick response)</em></div>`;
    }
    const lines = text.split("\n");
    const blocks = [];
    let inCodeBlock = false;
    let codeBlockLang = "";
    let codeBlockLines = [];
    let paraLines = [];
    let quoteLines = [];
    let listItems = [];
    let listType = "ul";

    const flushPara = () => {
      if (paraLines.length > 0) {
        blocks.push(`<p class="pg-reply-md-p">${paraLines.map(ln => (typeof renderInlineMarkdownHtml === "function" ? renderInlineMarkdownHtml(ln, false) : escapeHtml(ln))).join("<br/>")}</p>`);
        paraLines = [];
      }
    };
    const flushQuote = () => {
      if (quoteLines.length > 0) {
        blocks.push(`<blockquote class="pg-reply-md-quote">${quoteLines.map(ln => (typeof renderInlineMarkdownHtml === "function" ? renderInlineMarkdownHtml(ln, false) : escapeHtml(ln))).join("<br/>")}</blockquote>`);
        quoteLines = [];
      }
    };
    const flushList = () => {
      if (listItems.length > 0) {
        const itemsHtml = listItems.map(it => `<li class="pg-reply-md-li">${typeof renderInlineMarkdownHtml === "function" ? renderInlineMarkdownHtml(it, false) : escapeHtml(it)}</li>`).join("");
        blocks.push(`<${listType} class="pg-reply-md-list">${itemsHtml}</${listType}>`);
        listItems = [];
        listType = "ul";
      }
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();

      if (inCodeBlock) {
        if (trimmed.startsWith("```") || trimmed.startsWith("~~~")) {
          inCodeBlock = false;
          const codeContent = escapeHtml(codeBlockLines.join("\n"));
          const langBadge = codeBlockLang ? `<div class="pg-reply-md-lang">${escapeHtml(codeBlockLang)}</div>` : "";
          blocks.push(`<pre class="pg-reply-md-pre">${langBadge}<code>${codeContent}</code></pre>`);
          codeBlockLines = [];
          codeBlockLang = "";
        } else {
          codeBlockLines.push(line);
        }
        continue;
      }

      const fenceMatch = trimmed.match(/^(`{3,}|~{3,})(.*)$/);
      if (fenceMatch) {
        flushPara();
        flushQuote();
        flushList();
        inCodeBlock = true;
        codeBlockLang = fenceMatch[2].trim();
        codeBlockLines = [];
        continue;
      }

      if (!trimmed) {
        flushPara();
        flushQuote();
        flushList();
        continue;
      }

      // Heading (# .. ####)
      const hMatch = line.match(/^\s*(#{1,4})\s+(.+)$/);
      if (hMatch) {
        flushPara();
        flushQuote();
        flushList();
        const level = Math.min(4, hMatch[1].length);
        const headingHtml = typeof renderInlineMarkdownHtml === "function" ? renderInlineMarkdownHtml(hMatch[2].trim(), false) : escapeHtml(hMatch[2].trim());
        blocks.push(`<div class="pg-reply-md-h pg-reply-md-h${level}">${headingHtml}</div>`);
        continue;
      }

      // Blockquote (> ...)
      if (/^\s*>\s?/.test(line)) {
        flushPara();
        flushList();
        quoteLines.push(line.replace(/^\s*>\s?/, ""));
        continue;
      } else if (quoteLines.length > 0) {
        flushQuote();
      }

      // List (-, *, +, •, 1.)
      const listMatch = line.match(/^(\s*)([-*+•]|\d+\.)\s+(.+)$/);
      if (listMatch) {
        flushPara();
        flushQuote();
        const isNum = /\d+\./.test(listMatch[2]);
        const currentType = isNum ? "ol" : "ul";
        if (listItems.length > 0 && listType !== currentType) {
          flushList();
        }
        listType = currentType;
        listItems.push(listMatch[3]);
        continue;
      } else if (listItems.length > 0) {
        flushList();
      }

      paraLines.push(line);
    }

    if (inCodeBlock && codeBlockLines.length > 0) {
      const codeContent = escapeHtml(codeBlockLines.join("\n"));
      blocks.push(`<pre class="pg-reply-md-pre"><code>${codeContent}</code></pre>`);
    }
    flushPara();
    flushQuote();
    flushList();

    return `<div class="pg-reply-markdown-rendered">${blocks.join("")}</div>`;
  }

  function renderPgReplyMessageLivePreview() {
    if (typeof document === "undefined") return;
    const previewEl = document.getElementById("pgReplyMessagePreview");
    if (!previewEl) return;
    const ta = document.getElementById("stagedVoteCommentInput");
    const rawText = (ta && ta.value !== undefined) ? ta.value : ((state.stagedVote && state.stagedVote.comment) || "");
    previewEl.innerHTML = renderPgReplyMessageMarkdownHtml(rawText);
  }

  function toggleStagedVoteNote() {
    if (state.votingInFlight) return;
    ensureStagedVoteContextSynced();
    state.stagedVote.noteOpen = !state.stagedVote.noteOpen;
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    if (state.stagedVote.noteOpen) {
      const inp = document.getElementById("stagedVoteCommentInput");
      if (inp) inp.focus();
    }
  }

  function onStagedVoteCommentInput(val) {
    ensureStagedVoteContextSynced();
    state.stagedVote.comment = String(val || "");
    schedulePgReplyDialogDraftAutosave();
    syncActiveActionButtons(getActiveCommitsForDiff(), false);
    syncPgReplyQuickChipsActiveState();
    if (typeof getPgReplyMessageTab === "function" && getPgReplyMessageTab() === "preview" && typeof renderPgReplyMessageLivePreview === "function") {
      renderPgReplyMessageLivePreview();
    }
  }

  function onStagedVoteCommentKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (state.replyDialogOpen) {
        cancelReplyDialog();
        return;
      }
      state.stagedVote.noteOpen = false;
      if (event.target && typeof event.target.blur === "function") {
        event.target.blur();
      }
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
      return;
    }
    if (event.altKey && (event.key === "l" || event.key === "L")) {
      event.preventDefault();
      applyPgReplyQuickPreset("lgtm_cr2");
      return;
    }
    if (event.altKey && (event.key === "p" || event.key === "P")) {
      event.preventDefault();
      togglePgReplyMessageTab();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey) {
      const keyLower = (event.key || "").toLowerCase();
      if (keyLower === "b") {
        event.preventDefault();
        formatPgReplyCommentMarkdown("bold");
        return;
      }
      if (keyLower === "i") {
        event.preventDefault();
        formatPgReplyCommentMarkdown("italic");
        return;
      }
      if (keyLower === "e") {
        event.preventDefault();
        formatPgReplyCommentMarkdown("code");
        return;
      }
      if (keyLower === "k") {
        event.preventDefault();
        formatPgReplyCommentMarkdown("link");
        return;
      }
    }
    const isTextarea = Boolean(event.target && event.target.tagName === "TEXTAREA");
    if (isTextarea) {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        submitStagedVotes();
      }
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitStagedVotes();
    }
  }

  const _PG_EMPTY_STAGED_LABELS_MAP = Object.freeze({});

  function computeStagedVotePayload(commitsOverride = null) {
    const { isBatch, clNums, primaryCl } = ensureStagedVoteContextSynced(commitsOverride);
    const sv = state.stagedVote;
    if (
      sv.cr == null && sv.v == null && sv.pv == null && sv.pr == null &&
      sv.as == null && sv.perf == null && sv.lint == null && sv.bp == null &&
      sv.api == null && sv.oo == null && sv.rs == null && sv.cq == null &&
      sv.osl == null && sv.trust == null && !sv.comment &&
      !hasAnyOwnProp(sv.extraLabels)
    ) {
      const pendingDrafts = (!isBatch && primaryCl)
        ? (primaryCl._renderPassDrafts || getPendingDraftsForCl(primaryCl.number))
        : _PG_EMPTY_ARRAY;
      if (pendingDrafts.length === 0) {
        const extraRef = sv.extraLabels || _PG_EMPTY_STAGED_LABELS_MAP;
        if (
          !isBatch &&
          primaryCl &&
          primaryCl._cleanStagedVotePayload &&
          primaryCl._cleanStagedVotePayload.clNums === clNums &&
          primaryCl._cleanStagedVotePayload.extraLabels === extraRef
        ) {
          return primaryCl._cleanStagedVotePayload;
        }
        const cleanPayload = {
          isBatch,
          clNums,
          primaryCl,
          codeReview: null,
          verified: null,
          presubmitVerified: null,
          presubmitReady: null,
          autosubmit: null,
          performance: null,
          lint: null,
          bypassPresubmit: null,
          apiReview: null,
          ownersOverride: null,
          rubberStamp: null,
          commitQueue: null,
          openSourceLicensing: null,
          trust: null,
          extraLabels: extraRef,
          labels: {},
          message: "",
          labelsSummary: _PG_EMPTY_ARRAY,
          pendingDrafts,
          hasAnyChange: false,
        };
        if (!isBatch && primaryCl) {
          primaryCl._cleanStagedVotePayload = cleanPayload;
        }
        return cleanPayload;
      }
    }
    const labelsSummary = [];
    let codeReview = null;
    let verified = null;
    let presubmitVerified = null;
    let presubmitReady = null;
    let autosubmit = null;
    let performance = null;
    let lint = null;
    let bypassPresubmit = null;
    let apiReview = null;
    let ownersOverride = null;
    let rubberStamp = null;
    let commitQueue = null;
    let openSourceLicensing = null;
    let trust = null;

    if (sv.cr !== null && sv.cr !== undefined) {
      codeReview = Number(sv.cr);
      labelsSummary.push(`CR ${codeReview > 0 ? "+" + codeReview : codeReview}`);
    }
    if (sv.v !== null && sv.v !== undefined) {
      verified = Number(sv.v);
      labelsSummary.push(`V ${verified > 0 ? "+" + verified : verified}`);
    }
    if (sv.pv !== null && sv.pv !== undefined) {
      presubmitVerified = Number(sv.pv);
      labelsSummary.push(`PV ${presubmitVerified > 0 ? "+" + presubmitVerified : presubmitVerified}`);
    }
    if (sv.pr !== null && sv.pr !== undefined) {
      presubmitReady = Number(sv.pr);
      labelsSummary.push(`PR ${presubmitReady > 0 ? "+" + presubmitReady : presubmitReady}`);
    }
    if (sv.as !== null && sv.as !== undefined) {
      autosubmit = Number(sv.as);
      labelsSummary.push(`AS ${autosubmit > 0 ? "+" + autosubmit : autosubmit}`);
    }
    if (sv.perf !== null && sv.perf !== undefined) {
      performance = Number(sv.perf);
      labelsSummary.push(`Perf ${performance > 0 ? "+" + performance : performance}`);
    }
    if (sv.lint !== null && sv.lint !== undefined) {
      lint = Number(sv.lint);
      labelsSummary.push(`Lint ${lint > 0 ? "+" + lint : lint}`);
    }
    if (sv.bp !== null && sv.bp !== undefined) {
      bypassPresubmit = Number(sv.bp);
      labelsSummary.push(`BP ${bypassPresubmit > 0 ? "+" + bypassPresubmit : bypassPresubmit}`);
    }
    if (sv.api !== null && sv.api !== undefined) {
      apiReview = Number(sv.api);
      labelsSummary.push(`API ${apiReview > 0 ? "+" + apiReview : apiReview}`);
    }
    if (sv.oo !== null && sv.oo !== undefined) {
      ownersOverride = Number(sv.oo);
      labelsSummary.push(`OO ${ownersOverride > 0 ? "+" + ownersOverride : ownersOverride}`);
    }
    if (sv.rs !== null && sv.rs !== undefined) {
      rubberStamp = Number(sv.rs);
      labelsSummary.push(`RS ${rubberStamp > 0 ? "+" + rubberStamp : rubberStamp}`);
    }
    if (sv.cq !== null && sv.cq !== undefined) {
      commitQueue = Number(sv.cq);
      labelsSummary.push(`CQ ${commitQueue > 0 ? "+" + commitQueue : commitQueue}`);
    }
    if (sv.osl !== null && sv.osl !== undefined) {
      openSourceLicensing = Number(sv.osl);
      labelsSummary.push(`OSL ${openSourceLicensing > 0 ? "+" + openSourceLicensing : openSourceLicensing}`);
    }
    if (sv.trust !== null && sv.trust !== undefined) {
      trust = Number(sv.trust);
      labelsSummary.push(`Trust ${trust > 0 ? "+" + trust : trust}`);
    }
    let hasAnyCustomLabel = false;
    if (sv.extraLabels && typeof sv.extraLabels === "object") {
      for (const lbl in sv.extraLabels) {
        if (!Object.prototype.hasOwnProperty.call(sv.extraLabels, lbl)) continue;
        const val = sv.extraLabels[lbl];
        if (val !== null && val !== undefined) {
          hasAnyCustomLabel = true;
          const numVal = Number(val);
          labelsSummary.push(`${lbl} ${numVal > 0 ? "+" + numVal : numVal}`);
        }
      }
    }
    const message = String(sv.comment || "").trim();
    const pendingDrafts = (!isBatch && primaryCl)
      ? (primaryCl._renderPassDrafts || getPendingDraftsForCl(primaryCl.number))
      : [];
    const hasAnyChange = (
      codeReview !== null ||
      verified !== null ||
      presubmitVerified !== null ||
      presubmitReady !== null ||
      autosubmit !== null ||
      performance !== null ||
      lint !== null ||
      bypassPresubmit !== null ||
      apiReview !== null ||
      ownersOverride !== null ||
      rubberStamp !== null ||
      commitQueue !== null ||
      openSourceLicensing !== null ||
      trust !== null ||
      hasAnyCustomLabel ||
      Boolean(message) ||
      pendingDrafts.length > 0
    );
    const labelsMap = {};
    if (codeReview !== null) labelsMap["Code-Review"] = codeReview;
    if (verified !== null) labelsMap["Verified"] = verified;
    if (presubmitVerified !== null) labelsMap["Presubmit-Verified"] = presubmitVerified;
    if (presubmitReady !== null) labelsMap["Presubmit-Ready"] = presubmitReady;
    if (autosubmit !== null) labelsMap["Autosubmit"] = autosubmit;
    if (performance !== null) labelsMap["Performance"] = performance;
    if (lint !== null) labelsMap["Lint"] = lint;
    if (bypassPresubmit !== null) labelsMap["Bypass-Presubmit"] = bypassPresubmit;
    if (apiReview !== null) labelsMap["API-Review"] = apiReview;
    if (ownersOverride !== null) labelsMap["Owners-Override"] = ownersOverride;
    if (rubberStamp !== null) labelsMap["Rubber-Stamp"] = rubberStamp;
    if (commitQueue !== null) labelsMap["Commit-Queue"] = commitQueue;
    if (openSourceLicensing !== null) labelsMap["Open-Source-Licensing"] = openSourceLicensing;
    if (trust !== null) labelsMap["Trust"] = trust;
    if (hasAnyCustomLabel) {
      for (const lbl in sv.extraLabels) {
        if (!Object.prototype.hasOwnProperty.call(sv.extraLabels, lbl)) continue;
        const val = sv.extraLabels[lbl];
        if (val !== null && val !== undefined) {
          labelsMap[lbl] = Number(val);
        }
      }
    }
    return {
      isBatch,
      clNums,
      primaryCl,
      codeReview,
      verified,
      presubmitVerified,
      presubmitReady,
      autosubmit,
      performance,
      lint,
      bypassPresubmit,
      apiReview,
      ownersOverride,
      rubberStamp,
      commitQueue,
      openSourceLicensing,
      trust,
      extraLabels: sv.extraLabels || {},
      labels: labelsMap,
      message,
      labelsSummary,
      pendingDrafts,
      hasAnyChange: hasAnyChange && clNums.length > 0,
    };
  }

  function setToolbarVoteButtonsDisabled(disabled) {
    const boolDisabled = Boolean(disabled);
    if (!boolDisabled && state._lastToolbarVoteBtnsDisabled === false && !state._toolbarVoteDomDirty) {
      return;
    }
    state._lastToolbarVoteBtnsDisabled = boolDisabled;
    state._toolbarVoteDomDirty = false;
    const hasExtended = Boolean(state.extendedVoteControlsOpen);
    CR_STAGE_BUTTON_IDS.forEach(({ id }) => {
      const els = document.querySelectorAll(`#${id}`);
      els.forEach(el => { el.disabled = boolDisabled; });
    });
    V_STAGE_BUTTON_IDS.forEach(({ id }) => {
      const els = document.querySelectorAll(`#${id}`);
      els.forEach(el => { el.disabled = boolDisabled; });
    });
    PV_STAGE_BUTTON_IDS.forEach(({ id }) => {
      const els = document.querySelectorAll(`#${id}`);
      els.forEach(el => { el.disabled = boolDisabled; });
    });
    if (hasExtended || boolDisabled) {
      PERF_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      LINT_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      BP_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      API_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      OO_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      RS_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      CQ_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      OSL_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      TRUST_STAGE_BUTTON_IDS.forEach(({ id }) => {
        const els = document.querySelectorAll(`#${id}`);
        els.forEach(el => { el.disabled = boolDisabled; });
      });
      document.querySelectorAll("[data-custom-label]").forEach(el => {
        el.disabled = boolDisabled;
      });
    }
    const prCheck = document.getElementById("stagePrCheckbox");
    const asCheck = document.getElementById("stageAsCheckbox");
    const prZeroBtn = document.getElementById("pgPrZeroBtn");
    const prPlus1Btn = document.getElementById("pgPrPlus1Btn");
    const prPill = document.getElementById("stagePrPill");
    const asPill = document.getElementById("stageAsPill");
    const noteBtn = document.getElementById("btnToggleVoteNote");
    const noteInp = document.getElementById("stagedVoteCommentInput");
    const cancelBtn = document.getElementById("btnCancelReplyDialog");
    const openReplyBtn = document.getElementById("btnOpenReplyDialog");
    const submitBtn = document.getElementById("btnSubmitVotes");
    if (prCheck) prCheck.disabled = boolDisabled;
    if (asCheck) asCheck.disabled = boolDisabled;
    if (prZeroBtn) prZeroBtn.disabled = boolDisabled;
    if (prPlus1Btn) prPlus1Btn.disabled = boolDisabled;
    if (prPill) prPill.classList.toggle("is-disabled", boolDisabled);
    if (asPill) asPill.classList.toggle("is-disabled", boolDisabled);
    if (noteBtn) noteBtn.disabled = boolDisabled;
    if (noteInp) noteInp.disabled = boolDisabled;
    if (cancelBtn) cancelBtn.disabled = boolDisabled;
    if (openReplyBtn) openReplyBtn.disabled = boolDisabled;
    if (submitBtn && boolDisabled) submitBtn.disabled = true;
  }

  function syncBinaryStagePillDom({ checkId, pillId, labelId, stagedVal, curVal, isBatch, labelBase }) {
    const checkEl = document.getElementById(checkId);
    const pillEl = document.getElementById(pillId);
    const labelEl = document.getElementById(labelId);
    const effective = stagedVal !== null ? stagedVal : (isBatch ? 0 : curVal);
    if (checkEl) checkEl.checked = effective >= 1;
    if (pillEl) {
      pillEl.classList.toggle("is-staged", stagedVal !== null && stagedVal >= 1);
      pillEl.classList.toggle("is-staged-clear", stagedVal === 0);
      pillEl.classList.toggle("is-current", !isBatch && stagedVal === null && curVal >= 1);
    }
    if (labelEl) {
      labelEl.textContent = stagedVal === 0 ? `${labelBase} 0` : `${labelBase} +1`;
    }
  }

  function applyStageButtonAttrs(btn, isStaged, isCurrent) {
    const pressedStr = (isStaged || isCurrent) ? "true" : "false";
    const nextMask = (isStaged ? 2 : 0) | (isCurrent ? 1 : 0);
    if (btn._lastStageAttrMask === nextMask && btn.getAttribute("aria-pressed") === pressedStr) {
      return;
    }
    btn._lastStageAttrMask = nextMask;
    btn.classList.toggle("is-staged", isStaged);
    btn.classList.toggle("is-current", isCurrent);
    btn.setAttribute("aria-pressed", pressedStr);
  }

  function syncStageButtonGroupDom(btnDefs, stagedVal, curVal, isBatch) {
    const isMultiCommit = isContinuousStackDiffActive();
    for (let i = 0; i < btnDefs.length; i++) {
      const { val, id } = btnDefs[i];
      const isStaged = stagedVal === val;
      const isCurrent = !isBatch && stagedVal === null && curVal === val;
      if (!isMultiCommit) {
        const singleBtn = document.getElementById(id);
        if (singleBtn) {
          applyStageButtonAttrs(singleBtn, isStaged, isCurrent);
          continue;
        }
      }
      const btns = document.querySelectorAll(`#${id}`);
      for (let j = 0; j < btns.length; j++) {
        applyStageButtonAttrs(btns[j], isStaged, isCurrent);
      }
    }
  }

  function syncStagedVoteControlsDom(payload) {
    const { isBatch, clNums, primaryCl } = payload;
    const sv = state.stagedVote;
    const curCr = (!isBatch && primaryCl)
      ? Number(primaryCl.viewer_cr_vote || (primaryCl.approved_by_viewer ? 2 : 0))
      : null;
    const curV = (!isBatch && primaryCl)
      ? Number(primaryCl.viewer_verified_vote || 0)
      : null;
    const curPr = (!isBatch && primaryCl) ? getEffectiveCurrentViewerPr(primaryCl) : 0;
    const curAs = (!isBatch && primaryCl && primaryCl.viewer_autosubmit) ? 1 : 0;
    const curPv = (!isBatch && primaryCl) ? Number(primaryCl.viewer_presubmit_verified_vote || 0) : null;

    const hasExtendedControls = Boolean(
      state.extendedVoteControlsOpen ||
      (primaryCl && primaryCl.permitted_labels) ||
      sv.perf !== null || sv.lint !== null || sv.bp !== null ||
      sv.api !== null || sv.oo !== null || sv.rs !== null ||
      sv.cq !== null || sv.osl !== null || sv.trust !== null ||
      hasAnyOwnProp(sv.extraLabels)
    );
    const pendingDrafts = payload.pendingDrafts;
    const draftCount = pendingDrafts ? pendingDrafts.length : 0;
    const isMultiCommit = isContinuousStackDiffActive();
    const crZeroEl = !isMultiCommit ? document.getElementById("stageCrZero") : null;
    const fastCrZeroValid = Boolean(
      !isMultiCommit &&
      crZeroEl &&
      crZeroEl === state._lastStagedVoteCrZeroEl &&
      crZeroEl.getAttribute("aria-pressed") !== null
    );
    let curDiffGen = state._lastStagedVoteDiffGen || "";
    if (!fastCrZeroValid) {
      const diffEl = document.getElementById("diffScroll");
      const genNum = diffEl ? (diffEl._diffRenderGeneration || 0) : 0;
      const curDiffHtmlLen = (diffEl && !genNum && diffEl.innerHTML) ? diffEl.innerHTML.length : 0;
      curDiffGen = diffEl ? `${genNum}:${curDiffHtmlLen}` : "";
    }
    const clCount = isBatch ? clNums.length : 1;
    const scs = state._stagedCtrlSub;
    if (
      !hasExtendedControls &&
      !state._toolbarVoteDomDirty &&
      (fastCrZeroValid || state._lastStagedVoteDiffGen === curDiffGen) &&
      scs &&
      scs.isBatch === isBatch &&
      scs.clCount === clCount &&
      scs.curCr === curCr &&
      scs.curV === curV &&
      scs.curPr === curPr &&
      scs.curAs === curAs &&
      scs.curPv === curPv &&
      scs.svCr === sv.cr &&
      scs.svV === sv.v &&
      scs.svPr === sv.pr &&
      scs.svAs === sv.as &&
      scs.svPv === sv.pv &&
      scs.noteOpen === sv.noteOpen &&
      scs.comment === (sv.comment || "") &&
      scs.hasChange === payload.hasAnyChange &&
      scs.lblLen === payload.labelsSummary.length &&
      payload.labelsSummary.length === 0 &&
      scs.draftCount === draftCount &&
      scs.msg === (payload.message || "") &&
      scs.isMulti === isMultiCommit
    ) {
      const targetClNum = primaryCl ? primaryCl.number : null;
      if (scs.targetClNum !== targetClNum || draftCount > 0 || state._hadNonEmptyReplyDraftsSync) {
        scs.targetClNum = targetClNum;
        updateReplyButtonDraftsBadge(targetClNum, pendingDrafts, true);
        syncReplyDraftsSectionDom(targetClNum, pendingDrafts);
      }
      return;
    }
    const stagedControlsSig = !hasExtendedControls
      ? `${isBatch ? 1 : 0}|${clCount}|${curCr ?? ""}|${curV ?? ""}|${curPr}|${curAs}|${curPv ?? ""}|${sv.cr ?? ""}|${sv.v ?? ""}|${sv.pr ?? ""}|${sv.as ?? ""}|${sv.pv ?? ""}|${sv.noteOpen ? 1 : 0}|${sv.comment || ""}|${payload.hasAnyChange ? 1 : 0}|${payload.labelsSummary.join(",")}|${draftCount}|${payload.message || ""}|${isMultiCommit ? 1 : 0}`
      : "";
    if (
      stagedControlsSig &&
      !state._toolbarVoteDomDirty &&
      state._lastStagedVoteDomSyncSig === stagedControlsSig &&
      (fastCrZeroValid || state._lastStagedVoteDiffGen === curDiffGen)
    ) {
      const targetClNum = primaryCl ? primaryCl.number : null;
      if (!scs || scs.targetClNum !== targetClNum || draftCount > 0 || state._hadNonEmptyReplyDraftsSync) {
        if (scs) scs.targetClNum = targetClNum;
        updateReplyButtonDraftsBadge(targetClNum, pendingDrafts, true);
        syncReplyDraftsSectionDom(targetClNum, pendingDrafts);
      }
      return;
    }
    state._lastStagedVoteDomSyncSig = stagedControlsSig;
    state._lastStagedVoteDiffGen = curDiffGen;
    state._lastStagedVoteCrZeroEl = crZeroEl;
    if (!hasExtendedControls && payload.labelsSummary.length === 0) {
      const wScs = state._stagedCtrlSub || (state._stagedCtrlSub = {});
      wScs.targetClNum = primaryCl ? primaryCl.number : null;
      wScs.isBatch = isBatch;
      wScs.clCount = clCount;
      wScs.curCr = curCr;
      wScs.curV = curV;
      wScs.curPr = curPr;
      wScs.curAs = curAs;
      wScs.curPv = curPv;
      wScs.svCr = sv.cr;
      wScs.svV = sv.v;
      wScs.svPr = sv.pr;
      wScs.svAs = sv.as;
      wScs.svPv = sv.pv;
      wScs.noteOpen = sv.noteOpen;
      wScs.comment = sv.comment || "";
      wScs.hasChange = payload.hasAnyChange;
      wScs.lblLen = 0;
      wScs.draftCount = draftCount;
      wScs.msg = payload.message || "";
      wScs.isMulti = isMultiCommit;
    } else {
      state._stagedCtrlSub = null;
    }

    syncStageButtonGroupDom(CR_STAGE_BUTTON_IDS, sv.cr, curCr, isBatch);
    syncStageButtonGroupDom(V_STAGE_BUTTON_IDS, sv.v, curV, isBatch);

    syncBinaryStagePillDom({
      checkId: "stagePrCheckbox",
      pillId: "stagePrPill",
      labelId: "stagePrLabel",
      stagedVal: sv.pr,
      curVal: curPr,
      isBatch,
      labelBase: "Presubmit-Ready",
    });
    syncBinaryStagePillDom({
      checkId: "stageAsCheckbox",
      pillId: "stageAsPill",
      labelId: "stageAsLabel",
      stagedVal: sv.as,
      curVal: curAs,
      isBatch,
      labelBase: "Autosubmit",
    });

    syncStageButtonGroupDom(PV_STAGE_BUTTON_IDS, sv.pv, curPv, isBatch);

    if (hasExtendedControls) {
      const curPerf = (!isBatch && primaryCl) ? Number(primaryCl.viewer_performance_vote || 0) : null;
      const curLint = (!isBatch && primaryCl) ? Number(primaryCl.viewer_lint_vote || 0) : null;
      const curBp = (!isBatch && primaryCl) ? Number(primaryCl.viewer_bypass_presubmit_vote || 0) : null;
      const curApi = (!isBatch && primaryCl) ? Number(primaryCl.viewer_api_review_vote || 0) : null;
      const curOo = (!isBatch && primaryCl) ? Number(primaryCl.viewer_owners_override_vote || 0) : null;
      const curRs = (!isBatch && primaryCl) ? Number(primaryCl.viewer_rubber_stamp_vote || 0) : null;
      const curCq = (!isBatch && primaryCl) ? Number(primaryCl.viewer_commit_queue_vote || 0) : null;
      const curOsl = (!isBatch && primaryCl) ? Number(primaryCl.viewer_open_source_licensing_vote || 0) : null;
      const curTrust = (!isBatch && primaryCl) ? Number(primaryCl.viewer_trust_vote || 0) : null;

      syncStageButtonGroupDom(PERF_STAGE_BUTTON_IDS, sv.perf, curPerf, isBatch);
      syncStageButtonGroupDom(LINT_STAGE_BUTTON_IDS, sv.lint, curLint, isBatch);
      syncStageButtonGroupDom(BP_STAGE_BUTTON_IDS, sv.bp, curBp, isBatch);
      syncStageButtonGroupDom(API_STAGE_BUTTON_IDS, sv.api, curApi, isBatch);
      syncStageButtonGroupDom(OO_STAGE_BUTTON_IDS, sv.oo, curOo, isBatch);
      syncStageButtonGroupDom(RS_STAGE_BUTTON_IDS, sv.rs, curRs, isBatch);
      syncStageButtonGroupDom(CQ_STAGE_BUTTON_IDS, sv.cq, curCq, isBatch);
      syncStageButtonGroupDom(OSL_STAGE_BUTTON_IDS, sv.osl, curOsl, isBatch);
      syncStageButtonGroupDom(TRUST_STAGE_BUTTON_IDS, sv.trust, curTrust, isBatch);

      const customButtons = document.querySelectorAll("[data-custom-label]");
      customButtons.forEach(btn => {
        const lbl = btn.dataset.customLabel;
        const val = Number(btn.dataset.customVal);
        const isStaged = sv.extraLabels && sv.extraLabels[lbl] === val;
        btn.classList.toggle("is-staged", Boolean(isStaged));
        btn.setAttribute("aria-pressed", String(Boolean(isStaged)));
      });

      const effectivePerf = sv.perf !== null ? sv.perf : (curPerf !== null ? curPerf : 0);
      const perfDescEl = document.getElementById("pgPerfScoreDesc");
      if (perfDescEl) {
        perfDescEl.textContent = PG_PERF_SCORE_DESCRIPTIONS[String(effectivePerf)] || "";
      }

      const effectiveLint = sv.lint !== null ? sv.lint : (curLint !== null ? curLint : 0);
      const lintDescEl = document.getElementById("pgLintScoreDesc");
      if (lintDescEl) {
        lintDescEl.textContent = PG_LINT_SCORE_DESCRIPTIONS[String(effectiveLint)] || "";
      }

      const effectiveBp = sv.bp !== null ? sv.bp : (curBp !== null ? curBp : 0);
      const bpDescEl = document.getElementById("pgBpScoreDesc");
      if (bpDescEl) {
        bpDescEl.textContent = PG_BP_SCORE_DESCRIPTIONS[String(effectiveBp)] || "";
      }

      const effectiveApi = sv.api !== null ? sv.api : (curApi !== null ? curApi : 0);
      const apiDescEl = document.getElementById("pgApiScoreDesc");
      if (apiDescEl) {
        apiDescEl.textContent = PG_API_SCORE_DESCRIPTIONS[String(effectiveApi)] || "";
      }

      const effectiveOo = sv.oo !== null ? sv.oo : (curOo !== null ? curOo : 0);
      const ooDescEl = document.getElementById("pgOoScoreDesc");
      if (ooDescEl) {
        ooDescEl.textContent = PG_OO_SCORE_DESCRIPTIONS[String(effectiveOo)] || "";
      }

      const effectiveRs = sv.rs !== null ? sv.rs : (curRs !== null ? curRs : 0);
      const rsDescEl = document.getElementById("pgRsScoreDesc");
      if (rsDescEl) {
        rsDescEl.textContent = PG_RS_SCORE_DESCRIPTIONS[String(effectiveRs)] || "";
      }

      const effectiveCq = sv.cq !== null ? sv.cq : (curCq !== null ? curCq : 0);
      const cqDescEl = document.getElementById("pgCqScoreDesc");
      if (cqDescEl) {
        cqDescEl.textContent = PG_CQ_SCORE_DESCRIPTIONS[String(effectiveCq)] || "";
      }

      const effectiveOsl = sv.osl !== null ? sv.osl : (curOsl !== null ? curOsl : 0);
      const oslDescEl = document.getElementById("pgOslScoreDesc");
      if (oslDescEl) {
        oslDescEl.textContent = PG_OSL_SCORE_DESCRIPTIONS[String(effectiveOsl)] || "";
      }

      const effectiveTrust = sv.trust !== null ? sv.trust : (curTrust !== null ? curTrust : 0);
      const trustDescEl = document.getElementById("pgTrustScoreDesc");
      if (trustDescEl) {
        trustDescEl.textContent = PG_TRUST_SCORE_DESCRIPTIONS[String(effectiveTrust)] || "";
      }
    }

    const effectiveCr = sv.cr !== null ? sv.cr : (curCr !== null ? curCr : 0);
    const crDescEl = document.getElementById("pgCrScoreDesc");
    if (crDescEl) {
      crDescEl.textContent = PG_CR_SCORE_DESCRIPTIONS[String(effectiveCr)] || "";
    }

    const effectiveV = sv.v !== null ? sv.v : (curV !== null ? curV : 0);
    const vDescEl = document.getElementById("pgVScoreDesc");
    if (vDescEl) {
      vDescEl.textContent = PG_V_SCORE_DESCRIPTIONS[String(effectiveV)] || "";
    }

    const effectivePv = sv.pv !== null ? sv.pv : (curPv !== null ? curPv : 0);
    const pvDescEl = document.getElementById("pgPvScoreDesc");
    if (pvDescEl) {
      pvDescEl.textContent = PG_PV_SCORE_DESCRIPTIONS[String(effectivePv)] || "";
    }

    const prZeroBtn = document.getElementById("pgPrZeroBtn");
    const prPlus1Btn = document.getElementById("pgPrPlus1Btn");
    const effectivePr = sv.pr !== null ? sv.pr : curPr;
    if (prZeroBtn) {
      prZeroBtn.classList.toggle("is-staged", sv.pr === 0);
      prZeroBtn.classList.toggle("is-current", !isBatch && sv.pr === null && curPr === 0);
      prZeroBtn.setAttribute("aria-pressed", String(effectivePr === 0));
    }
    if (prPlus1Btn) {
      prPlus1Btn.classList.toggle("is-staged", sv.pr === 1);
      prPlus1Btn.classList.toggle("is-current", !isBatch && sv.pr === null && curPr >= 1);
      prPlus1Btn.setAttribute("aria-pressed", String(effectivePr >= 1));
    }
    const prDescEl = document.getElementById("pgPrScoreDesc");
    if (prDescEl) {
      prDescEl.textContent = effectivePr >= 1 ? "Ready for presubmit" : "Not ready for presubmit";
    }

    const stagedHintEl = document.getElementById("pgReplyStagedHint");
    if (stagedHintEl) {
      stagedHintEl.textContent = (!isBatch && payload.hasAnyChange)
        ? `Draft: ${payload.labelsSummary.join(", ") || "Comment"}`
        : "";
    }

    const noteBtn = document.getElementById("btnToggleVoteNote");
    const noteRow = document.getElementById("stagedVoteNoteRow");
    const noteInp = document.getElementById("stagedVoteCommentInput");
    const showNote = Boolean(sv.noteOpen || (sv.comment || "").trim());
    if (noteBtn) noteBtn.classList.toggle("has-note", Boolean((sv.comment || "").trim()));
    if (noteRow) noteRow.style.display = showNote ? "flex" : "none";
    if (noteInp && noteInp.value !== (sv.comment || "")) noteInp.value = sv.comment || "";

    const resetBtn = document.getElementById("btnResetStagedVotes");
    if (resetBtn) resetBtn.style.display = payload.hasAnyChange ? "" : "none";

    const submitBtn = document.getElementById("btnSubmitVotes");
    if (submitBtn) {
      submitBtn.disabled = !payload.hasAnyChange;
      submitBtn.classList.toggle("is-ready", Boolean(payload.hasAnyChange));
      const summaryPart = payload.labelsSummary.length > 0 ? payload.labelsSummary.join(", ") : "Comment";
      if (isBatch) {
        submitBtn.textContent = payload.hasAnyChange
          ? `Send to ${clNums.length} CL${clNums.length === 1 ? "" : "s"} (${summaryPart})`
          : `Send (${clNums.length} CL${clNums.length === 1 ? "" : "s"})`;
      } else {
        const draftCount = payload.pendingDrafts ? payload.pendingDrafts.length : 0;
        let sendLabel = "Send";
        if (payload.labelsSummary.length > 0) {
          sendLabel = draftCount > 0
            ? `Send (${payload.labelsSummary.join(", ")} + ${draftCount} draft${draftCount > 1 ? "s" : ""})`
            : `Send (${payload.labelsSummary.join(", ")})`;
        } else if (draftCount > 0) {
          sendLabel = `Send (${draftCount} draft${draftCount > 1 ? "s" : ""})`;
        } else if (payload.message) {
          sendLabel = "Send (Comment)";
        }
        submitBtn.textContent = sendLabel;
      }
    }
    updateReplyButtonDraftsBadge(payload.primaryCl ? payload.primaryCl.number : null, payload.pendingDrafts, true);
    syncReplyDraftsSectionDom(payload.primaryCl ? payload.primaryCl.number : null, payload.pendingDrafts);
  }

  function syncActiveActionButtons(commits, isDiffLoading) {
    syncToolbarNavButtons();
    syncAiAnalysisButton(commits);
    syncSubmitClButtonsDom(commits);
    if (typeof syncAemuMapButtons === "function") {
      syncAemuMapButtons(commits);
    }
    const btnAutoFix = document.getElementById("btnAutoFix");
    if (btnAutoFix && !btnAutoFix.classList.contains("is-running-action")) {
      btnAutoFix.disabled = Boolean(state.queueLoading || commits.length === 0);
    }
    if (state.votingInFlight || state.submittingInFlight || state.queueLoading) return;

    const payload = computeStagedVotePayload(commits);
    const { isBatch, clNums, primaryCl } = payload;
    const voteRowTitle = document.getElementById("voteRowTitle");
    const batchHeaderStrip = document.getElementById("batchVoteHeaderStrip");
    const targetBadge = document.getElementById("voteTargetBadge");
    const batchActionsEl = document.getElementById("batchParticipantActions");

    const batchCount = isBatch ? clNums.length : 0;
    if (voteRowTitle && voteRowTitle._lastBatchCount !== batchCount) {
      voteRowTitle._lastBatchCount = batchCount;
      voteRowTitle.textContent = isBatch ? `Batch (${batchCount})` : "Your Vote";
      voteRowTitle.classList.toggle("is-batch-title", isBatch);
    }
    const stripDisp = isBatch ? "flex" : "none";
    if (batchHeaderStrip && batchHeaderStrip.style.display !== stripDisp) {
      batchHeaderStrip.style.display = stripDisp;
    }
    const actDisp = isBatch ? "inline-flex" : "none";
    if (batchActionsEl && batchActionsEl.style.display !== actDisp) {
      batchActionsEl.style.display = actDisp;
    }

    if (clNums.length === 0) {
      setToolbarVoteButtonsDisabled(true);
      if (targetBadge && targetBadge._lastTargetKey !== "empty") {
        targetBadge._lastTargetKey = "empty";
        targetBadge.className = "vote-target-pill";
        targetBadge.textContent = "Queue Clear";
      }
      const submitBtn = document.getElementById("btnSubmitVotes");
      const resetBtn = document.getElementById("btnResetStagedVotes");
      if (submitBtn) submitBtn.textContent = "Submit Vote";
      if (resetBtn) resetBtn.style.display = "none";
      return;
    }

    if (targetBadge) {
      const targetKey = isBatch ? `batch:${clNums.length}` : `single:${primaryCl.number}`;
      if (targetBadge._lastTargetKey !== targetKey) {
        targetBadge._lastTargetKey = targetKey;
        if (isBatch) {
          targetBadge.className = "vote-target-pill is-batch";
          targetBadge.innerHTML = `<span>☑ Applying to ${clNums.length} Checked CL${clNums.length === 1 ? "" : "s"}</span><button type="button" class="vote-target-clear" onclick="event.stopPropagation(); clearCheckedCls()" title="Clear checked CLs">✕</button>`;
        } else {
          targetBadge.className = "vote-target-pill";
          targetBadge.textContent = `ag/${primaryCl.number}`;
        }
      }
    }

    if (!isBatch && isDiffLoading) {
      setToolbarVoteButtonsDisabled(true);
      const submitBtn = document.getElementById("btnSubmitVotes");
      if (submitBtn) {
        submitBtn.innerHTML = `<span class="btn-spinner"></span> Loading ag/${primaryCl.number}…`;
      }
      return;
    }

    setToolbarVoteButtonsDisabled(false);
    syncStagedVoteControlsDom(payload);
    state._toolbarVoteDomDirty = false;
  }

  async function submitStagedVotes(btnEl) {
    if (state.votingInFlight || state.submittingInFlight) return;
    const payload = computeStagedVotePayload();
    const primaryCl = payload.primaryCl;
    const pendingDrafts = (!payload.isBatch && primaryCl)
      ? getPendingDraftsForCl(primaryCl.number)
      : [];

    if (!payload.hasAnyChange && pendingDrafts.length === 0) {
      showToast("Check at least one vote label (CR, V, PR +1, AS +1), enter a review comment, or add draft comments first.");
      return;
    }

    const activeBtn = btnEl || document.getElementById("btnSubmitVotes");
    if (pendingDrafts.length > 0 && primaryCl) {
      if (activeBtn) {
        activeBtn.disabled = true;
        activeBtn.innerHTML = `<span class="btn-spinner"></span> Publishing ${pendingDrafts.length} draft${pendingDrafts.length > 1 ? "s" : ""}…`;
      }
      try {
        await publishPendingDraftsForCl(primaryCl.number, pendingDrafts);
      } catch (err) {
        if (activeBtn) {
          activeBtn.disabled = false;
          activeBtn.textContent = "Send";
        }
        showToast("Draft publishing failed: " + (err.message || err));
        return;
      }
    }

    // Dispatch attention set modifications from reply dialog
    const attnCheckboxes = Array.from(document.querySelectorAll(".pg-reply-attn-cb"));
    const attnReasonInput = document.getElementById("pgReplyAttentionReasonInput");
    const customAttnReason = attnReasonInput ? attnReasonInput.value.trim() : "";
    if (primaryCl && !payload.isBatch && attnCheckboxes.length > 0) {
      attnCheckboxes.forEach(cb => {
        const targetEmail = cb.getAttribute("data-email");
        const targetUser = cb.getAttribute("data-user") || targetEmail;
        const isChecked = Boolean(cb.checked);
        const wasInAttn = isEmailInClAttentionSet(primaryCl, targetEmail);
        if (isChecked && !wasInAttn) {
          sidecarFetch("/api/gerrit/attention", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cl: primaryCl.number, action: "add", user: targetUser, reason: customAttnReason || "Replying on change", viewer: state.viewer }),
          }).catch(() => {});
        } else if (!isChecked && wasInAttn) {
          sidecarFetch("/api/gerrit/attention", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cl: primaryCl.number, action: "remove", user: targetUser, reason: customAttnReason || "Replying on change", viewer: state.viewer }),
          }).catch(() => {});
        }
      });
    }

    const hasVoteOrMessage = payload.codeReview !== null ||
      payload.verified !== null ||
      payload.presubmitVerified !== null ||
      payload.presubmitReady !== null ||
      payload.autosubmit !== null ||
      payload.performance !== null ||
      payload.lint !== null ||
      payload.bypassPresubmit !== null ||
      payload.apiReview !== null ||
      payload.ownersOverride !== null ||
      payload.rubberStamp !== null ||
      payload.commitQueue !== null ||
      payload.openSourceLicensing !== null ||
      payload.trust !== null ||
      payload.hasAnyCustomLabel ||
      Boolean(payload.message);

    if (hasVoteOrMessage || payload.isBatch) {
      castVotes(
        payload.clNums,
        activeBtn,
        {
          codeReview: payload.codeReview,
          verified: payload.verified,
          presubmitVerified: payload.presubmitVerified,
          presubmitReady: payload.presubmitReady,
          autosubmit: payload.autosubmit,
          performance: payload.performance,
          lint: payload.lint,
          bypassPresubmit: payload.bypassPresubmit,
          apiReview: payload.apiReview,
          ownersOverride: payload.ownersOverride,
          rubberStamp: payload.rubberStamp,
          commitQueue: payload.commitQueue,
          openSourceLicensing: payload.openSourceLicensing,
          trust: payload.trust,
          labels: payload.labels,
          message: payload.message,
        }
      );
    } else {
      clearStagedVoteFieldsAfterSend();
      closeReplyDialog();
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
      updateReplyButtonDraftsBadge(primaryCl ? primaryCl.number : null);
      showToast(`✓ Published ${pendingDrafts.length} draft comment${pendingDrafts.length > 1 ? "s" : ""} (notify: OWNER)!`);
      renderDiffPane({ preserveScroll: true });
    }
  }

  const ACTIVE_DIFF_QUESTION_POLL_THROTTLE_MS = 250;

  function collectMissingDiffClNumbers(commits, isBootstrap) {
    if (isBootstrap) return _PG_EMPTY_ARR;
    let missing = _PG_EMPTY_ARR;
    for (let i = 0; i < commits.length; i++) {
      const num = commits[i].number;
      if (!state.diffsByCl[num]) {
        if (missing === _PG_EMPTY_ARR) missing = [];
        missing.push(num);
      }
    }
    return missing;
  }

  function prefetchAdjacentVisibleDiff(primary, pNum) {
    const ordered = state._renderPassOrderedVisible || getOrderedVisibleCommits(getVisibleStacks());
    const ordLen = ordered.length;
    if (ordLen <= 1) return;
    let idxMap = ordered._idxByClMap;
    if (!idxMap) {
      idxMap = new Map();
      for (let i = 0; i < ordLen; i++) {
        const it = ordered[i];
        if (it && it.commit) idxMap.set(Number(it.commit.number), i);
      }
      ordered._idxByClMap = idxMap;
    }
    const curIdx = idxMap.get(pNum);
    if (curIdx === undefined) return;
    const nextItem = ordered[(curIdx + 1) % ordLen];
    const nextNum = nextItem ? Number(nextItem.commit.number) : 0;
    if (nextNum && nextNum !== pNum && !state.diffsByCl[nextNum]) {
      prefetchDiffsForNumbers([nextItem.commit.number]);
    }
  }

  async function ensureActiveDiffsLoaded(opts = {}) {
    const commits = getActiveCommitsForDiff();
    if (commits.length === 0) {
      syncActiveActionButtons([], false);
      renderDiffPane(opts);
      return;
    }
    const primary = commits.length === 1
      ? commits[0]
      : (commits.find(c => c.number === state.selectedClNumber) || commits[0]);
    const openGerritEl = document.getElementById("openGerritLink");
    if (openGerritEl && openGerritEl._lastHref !== primary.url) {
      openGerritEl._lastHref = primary.url;
      openGerritEl.href = primary.url;
    }
    const openAemuMapEl = document.getElementById("openAemuMapLink");
    if (openAemuMapEl && primary && typeof buildAemuMapCommitUrl === "function") {
      const mapUrl = buildAemuMapCommitUrl(primary, (state.diffsByCl && state.diffsByCl[primary.number]) || null);
      if (openAemuMapEl._lastMapHref !== mapUrl) {
        openAemuMapEl._lastMapHref = mapUrl;
        openAemuMapEl.href = mapUrl;
      }
    }
    const isBootstrap = Boolean(window.__GERRIT_UI_BOOTSTRAP__ && window.__GERRIT_UI_BOOTSTRAP__.changes);
    const pNum = Number(primary.number);
    if (!isBootstrap && !state.autoExpandedPresubmitCls.has(pNum)) {
      if (primary.presubmit_status === "running" || primary.presubmit_status === "failed") {
        state.autoExpandedPresubmitCls.add(pNum);
        state.expandedPresubmitCls.add(pNum);
      }
    }

    const missing = collectMissingDiffClNumbers(commits, isBootstrap);
    const needsFetch = missing.length > 0 && !isBootstrap;
    if (needsFetch) {
      syncActiveActionButtons(commits, true);
      renderDiffPane(opts);
      await prefetchDiffsForNumbers(missing, {
        trackLoading: true,
        label: `Fetching unified diff for ${missing.map(n => "ag/" + n).join(", ")}…`,
      });
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
      renderDiffPane({ preserveScroll: Boolean(opts.preserveScroll) });
      if (!opts.preserveScroll && isContinuousStackDiffActive() && state.selectedClNumber) {
        scrollToStackCommit(state.selectedClNumber);
      }
    } else {
      renderDiffPane(opts);
      if (!opts.preserveScroll && isContinuousStackDiffActive() && state.selectedClNumber) {
        scrollToStackCommit(state.selectedClNumber);
      }
    }
    if (!isBootstrap) {
      const loadedMergePs = state._loadedMergeablePsByCl;
      for (let i = 0; i < commits.length; i++) {
        const c = commits[i];
        const cNum = Number(c.number);
        if (state.expandedPresubmitCls.has(cNum) && !state.presubmitTargetsByCl[cNum]) {
          fetchPresubmitTargetsForCl(cNum);
        }
        const curClPs = Number(c.patch_set || 1);
        const alreadyLoadedMerge = Boolean(
          (state.mergeabilityByCl && state.mergeabilityByCl[cNum]) ||
          (loadedMergePs && loadedMergePs[cNum] === curClPs)
        );
        if (!alreadyLoadedMerge && typeof loadPgMergeable === "function" && cNum && !isClMerged(c) && !isClAbandoned(c)) {
          loadPgMergeable(cNum);
        }
      }
      ensurePresubmitTargetsPollingTimer();
      fetchCachedAiAnalysisForCommits(commits);
      pollTranscriptCodeQuestions(commits, { throttleMs: ACTIVE_DIFF_QUESTION_POLL_THROTTLE_MS });
    }
    ensureCodeQuestionPollingTimer();
    ensureAiAnalysisPollingTimer();
    if (!opts.preserveScroll && !isBootstrap && Array.isArray(state.changes) && state.changes.length > 1) {
      prefetchAdjacentVisibleDiff(primary, pNum);
    }
  }

  const MAX_REPLY_NOTIFICATION_BUBBLES = 8;
  const QA_FLASH_DURATION_MS = 1850;
  const CODE_QUESTION_POLL_INTERVAL_MS = 1800;
  const AI_ANALYSIS_POLL_INTERVAL_MS = 2200;

  function syncHarnessMenuButton(commits) {
    const toggleBtn = document.getElementById("btnHarnessMenuToggle");
    const labelEl = document.getElementById("harnessMenuLabel");
    if (!toggleBtn || !labelEl) return;
    const list = Array.isArray(commits) ? commits : getActiveCommitsForDiff();
    if (state.queueLoading || list.length === 0) {
      if (toggleBtn._lastHarnessSyncSig === "empty" && !state.harnessMenuOpen) return;
      toggleBtn._lastHarnessSyncSig = "empty";
      toggleBtn.disabled = true;
      toggleBtn.classList.remove("is-thinking", "is-swarming", "has-ready", "is-open");
      labelEl.textContent = "⚡ Harness Actions";
      if (state.harnessMenuOpen) closeHarnessMenu();
      return;
    }
    const primary = list.length === 1 ? list[0] : (list.find(c => Number(c.number) === Number(state.selectedClNumber)) || list[0]);
    const pNum = Number(primary.number);
    const aiEntry = state.aiAnalysisByCl[pNum];
    const aiStatus = aiEntry ? aiEntry.analysis_status : "not_analyzed";
    const aiVisible = aiStatus === "ready" && !state.hiddenAiAnalysisCls.has(pNum);
    const selSwarm = state.swarmByCl[pNum];
    const swarmRunning = Boolean(selSwarm && selSwarm.state === "running");
    const swarmDone = swarmRunning ? Number(selSwarm.completed_specialists || 0) : 0;
    const swarmTotal = swarmRunning ? Number(selSwarm.total_specialists || 8) : 0;
    const swarmSec = swarmRunning ? getSwarmElapsedSec(selSwarm) : 0;
    const isAutoFixLaunching = Boolean(state.autoFixLaunchingCl && Number(state.autoFixLaunchingCl) === pNum);
    const menuOpen = Boolean(state.harnessMenuOpen);
    if (
      toggleBtn._lastHarnessSyncSig !== "empty" &&
      toggleBtn._lastHNum === pNum &&
      toggleBtn._lastHOpen === menuOpen &&
      toggleBtn._lastHAiSt === aiStatus &&
      toggleBtn._lastHAiVis === aiVisible &&
      toggleBtn._lastHSwarmRun === swarmRunning &&
      (!swarmRunning || (
        toggleBtn._lastHSwarmDone === swarmDone &&
        toggleBtn._lastHSwarmTotal === swarmTotal &&
        toggleBtn._lastHSwarmSec === swarmSec
      )) &&
      toggleBtn._lastHAutoFix === isAutoFixLaunching
    ) {
      return;
    }
    toggleBtn._lastHarnessSyncSig = "active";
    toggleBtn._lastHNum = pNum;
    toggleBtn._lastHOpen = menuOpen;
    toggleBtn._lastHAiSt = aiStatus;
    toggleBtn._lastHAiVis = aiVisible;
    toggleBtn._lastHSwarmRun = swarmRunning;
    toggleBtn._lastHSwarmDone = swarmDone;
    toggleBtn._lastHSwarmTotal = swarmTotal;
    toggleBtn._lastHSwarmSec = swarmSec;
    toggleBtn._lastHAutoFix = isAutoFixLaunching;

    toggleBtn.disabled = false;
    toggleBtn.classList.toggle("is-open", Boolean(state.harnessMenuOpen));
    toggleBtn.classList.toggle("is-thinking", aiStatus === "thinking");
    toggleBtn.classList.toggle("is-swarming", aiStatus !== "thinking" && swarmRunning);
    toggleBtn.classList.toggle("has-ready", aiStatus !== "thinking" && !swarmRunning && aiVisible);

    if (aiStatus === "thinking") {
      labelEl.innerHTML = `<span class="btn-spinner"></span> Analyzing Problem…`;
      toggleBtn.title = `Analyzing problem & alternatives on ag/${pNum} — open menu or panel to cancel`;
      return;
    }
    if (swarmRunning) {
      labelEl.innerHTML = `<span class="swarm-spinner"></span> Swarm · ${swarmDone}/${swarmTotal} (${swarmSec}s)`;
      toggleBtn.title = `8-Specialist Review Swarm running on ag/${pNum}`;
      return;
    }
    if (isAutoFixLaunching) {
      labelEl.innerHTML = `<span class="btn-spinner"></span> Launching Fix…`;
      toggleBtn.title = `Launching Auto-Address Comments on ag/${pNum}`;
      return;
    }
    labelEl.textContent = "⚡ Harness Actions";
    toggleBtn.title = `Open Harness Actions for ag/${pNum}`;
  }

  function positionHarnessDropdownMenu() {
    if (!state.harnessMenuOpen) return;
    const toggleBtn = document.getElementById("btnHarnessMenuToggle");
    const menuEl = document.getElementById("harnessDropdownMenu");
    if (!toggleBtn || !menuEl) return;

    const vw = window.innerWidth || document.documentElement.clientWidth || 1024;
    const vh = window.innerHeight || document.documentElement.clientHeight || 768;
    const btnRect = toggleBtn.getBoundingClientRect();
    const diffPane = toggleBtn.closest(".diff-pane");
    const paneLeft = diffPane ? diffPane.getBoundingClientRect().left : 8;

    const menuW = menuEl.offsetWidth || Math.min(344, vw - 16);
    const menuH = menuEl.offsetHeight || 190;

    // Right-align when there is room inside diff-pane; left-align when toolbar wraps to the left edge.
    const minPaneLeft = Math.max(8, paneLeft + 8);
    let left = (btnRect.right - menuW >= minPaneLeft)
      ? (btnRect.right - menuW)
      : btnRect.left;
    left = Math.max(8, Math.min(left, vw - menuW - 8));

    let top = btnRect.bottom + 6;
    if (top + menuH > vh - 8 && btnRect.top - menuH - 6 >= 8) {
      top = btnRect.top - menuH - 6;
    } else {
      top = Math.max(8, Math.min(top, vh - menuH - 8));
    }

    menuEl.style.left = `${Math.round(left)}px`;
    menuEl.style.top = `${Math.round(top)}px`;
  }

  function toggleHarnessMenu(event) {
    if (event) event.stopPropagation();
    const toggleBtn = document.getElementById("btnHarnessMenuToggle");
    if (toggleBtn && toggleBtn.disabled) return;
    if (state.harnessMenuOpen) {
      closeHarnessMenu();
      return;
    }
    state.harnessMenuOpen = true;
    const commits = getActiveCommitsForDiff();
    syncAiAnalysisButton(commits);
    syncSwarmIndicatorsOnly();
    const menuEl = document.getElementById("harnessDropdownMenu");
    if (menuEl) {
      menuEl.classList.add("open");
      menuEl.setAttribute("aria-hidden", "false");
    }
    if (toggleBtn) {
      toggleBtn.classList.add("is-open");
      toggleBtn.setAttribute("aria-expanded", "true");
    }
    positionHarnessDropdownMenu();
  }

  function closeHarnessMenu() {
    if (!state.harnessMenuOpen) return;
    state.harnessMenuOpen = false;
    const menuEl = document.getElementById("harnessDropdownMenu");
    if (menuEl) {
      menuEl.classList.remove("open");
      menuEl.setAttribute("aria-hidden", "true");
    }
    const toggleBtn = document.getElementById("btnHarnessMenuToggle");
    if (toggleBtn) {
      toggleBtn.classList.remove("is-open");
      toggleBtn.setAttribute("aria-expanded", "false");
    }
  }

  function onHarnessMenuAction(action, btnEl) {
    closeHarnessMenu();
    if (action === "problem-alternatives") {
      onAiAnalysisButtonClick();
      return;
    }
    triggerHarness(action, btnEl);
  }

  function syncAiAnalysisButton(commits) {
    const btn = document.getElementById("btnAiAnalysis");
    const list = Array.isArray(commits) ? commits : getActiveCommitsForDiff();
    syncHarnessMenuButton(list);
    if (!btn) return;
    if (state.queueLoading || list.length === 0) {
      if (btn._lastAiBtnSyncSig === "empty") return;
      btn._lastAiBtnSyncSig = "empty";
      btn.disabled = true;
      btn.classList.remove("is-thinking", "is-active");
      const labelEl = document.getElementById("btnAiAnalysisLabel");
      const subEl = document.getElementById("btnAiAnalysisSub");
      if (labelEl) labelEl.textContent = "🧠 Analyze Problem & Alternatives";
      if (subEl) subEl.textContent = "Underlying problem, chosen solution, tradeoffs & alternatives";
      return;
    }
    const primary = list.length === 1 ? list[0] : (list.find(c => Number(c.number) === Number(state.selectedClNumber)) || list[0]);
    const pNum = Number(primary.number);
    const entry = state.aiAnalysisByCl[pNum];
    const status = entry ? entry.analysis_status : "not_analyzed";
    const isHidden = state.hiddenAiAnalysisCls.has(pNum);
    if (
      btn._lastAiBtnSyncSig !== "empty" &&
      btn._lastAiPNum === pNum &&
      btn._lastAiStatus === status &&
      btn._lastAiHidden === isHidden
    ) {
      return;
    }
    const labelEl = document.getElementById("btnAiAnalysisLabel");
    const subEl = document.getElementById("btnAiAnalysisSub");
    btn._lastAiBtnSyncSig = "active";
    btn._lastAiPNum = pNum;
    btn._lastAiStatus = status;
    btn._lastAiHidden = isHidden;
    btn.disabled = false;

    btn.classList.toggle("is-thinking", status === "thinking");
    btn.classList.toggle("is-active", status === "ready" && !isHidden);
    if (status === "thinking") {
      if (labelEl) {
        labelEl.innerHTML = `<span class="btn-spinner"></span> Cancel Problem &amp; Alternatives Analysis`;
      }
      if (subEl) {
        subEl.textContent = `Analyzing ag/${pNum} — click to cancel`;
      }
      btn.title = `Cancel in-flight Problem & Alternatives analysis on ag/${pNum}`;
      return;
    }
    if (status === "ready") {
      if (labelEl) {
        labelEl.textContent = isHidden
          ? "🧠 Show Problem & Alternatives"
          : "✓ Hide Problem & Alternatives";
      }
      if (subEl) {
        subEl.textContent = isHidden
          ? `Cached for ag/${pNum} — click to display`
          : `Shown on ag/${pNum} — click to hide (use ↻ Re-run in panel to refresh)`;
      }
      btn.title = isHidden
        ? `Show cached Problem & Alternatives analysis for ag/${pNum}`
        : `Hide Problem & Alternatives panel for ag/${pNum}`;
      return;
    }
    if (labelEl) labelEl.textContent = "🧠 Analyze Problem & Alternatives";
    if (subEl) subEl.textContent = "Underlying problem, chosen solution, tradeoffs & alternatives";
    btn.title = `Analyze the underlying problem, chosen solution, tradeoffs, and alternative solutions for ag/${pNum}`;
  }

  function onAiAnalysisButtonClick() {
    const commits = getActiveCommitsForDiff();
    if (commits.length === 0) return;
    const primary = commits.find(c => Number(c.number) === Number(state.selectedClNumber)) || commits[0];
    const pNum = Number(primary.number);
    const existing = state.aiAnalysisByCl[pNum];
    const status = existing ? existing.analysis_status : "not_analyzed";

    if (status === "thinking") {
      cancelClAiAnalysis(pNum);
      return;
    }
    if (status === "ready" && existing.analysis) {
      if (state.hiddenAiAnalysisCls.has(pNum)) {
        state.hiddenAiAnalysisCls.delete(pNum);
        state.collapsedAiAnalysisCls.delete(pNum);
      } else {
        state.hiddenAiAnalysisCls.add(pNum);
      }
      syncAiAnalysisButton(commits);
      renderDiffPane({ preserveScroll: true });
      return;
    }
    analyzeClIntangibles(pNum, status === "error");
  }

  async function fetchCachedAiAnalysisForCommits(commits) {
    const isBootstrap = Boolean(window.__GERRIT_UI_BOOTSTRAP__ && window.__GERRIT_UI_BOOTSTRAP__.changes);
    if (isBootstrap) return;
    const list = Array.isArray(commits) ? commits : [];
    for (const c of list) {
      const cNum = Number(c.number);
      if (!cNum || state.aiAnalysisByCl[cNum] || state.inFlightAiAnalysisFetchByCl[cNum]) {
        continue;
      }
      fetchAiAnalysisStatusForCl(cNum, Number(c.patch_set || 0), { silentIfNotAnalyzed: true });
    }
  }

  async function fetchAiAnalysisStatusForCl(clNum, patchSet = 0, opts = {}) {
    const num = Number(clNum);
    if (!num || state.inFlightAiAnalysisFetchByCl[num] || state.inFlightAiAnalysisStartByCl[num]) {
      return null;
    }
    const reqSeq = state.aiAnalysisReqSeqByCl[num] || 0;
    const cObj = patchSet ? null : findPgChangeByNumber(num);
    const ps = patchSet || Number((cObj && cObj.patch_set) || 0);
    const prev = state.aiAnalysisByCl[num];
    const wasThinking = Boolean(prev && prev.analysis_status === "thinking");
    state.inFlightAiAnalysisFetchByCl[num] = true;
    try {
      const psParam = ps > 0 ? `&ps=${encodeURIComponent(String(ps))}` : "";
      const resp = await sidecarFetch(`/api/gerrit/ai-analysis?cl=${encodeURIComponent(String(num))}${psParam}`);
      if (!resp.ok) return null;
      const data = await resp.json();
      if (!data || data.error || (state.aiAnalysisReqSeqByCl[num] || 0) !== reqSeq) return null;
      state.aiAnalysisByCl[num] = data;
      syncAiAnalysisButton(getActiveCommitsForDiff());
      if (opts.silentIfNotAnalyzed && data.analysis_status === "not_analyzed") {
        return data;
      }
      handleAiAnalysisStateTransition(num, wasThinking, data);
      return data;
    } catch (_err) {
      return null;
    } finally {
      delete state.inFlightAiAnalysisFetchByCl[num];
    }
  }

  function handleAiAnalysisStateTransition(clNum, wasThinking, data) {
    const activeNums = getActiveCommitsForDiff().map(c => Number(c.number));
    const isVisible = activeNums.includes(Number(clNum));
    const composerBusy = Boolean(
      state.codeSelection || state.activeReviewerComposer || state.activeChatComposer
    );
    if (wasThinking && data.analysis_status === "ready") {
      const drawerEl = document.getElementById("drawerStatus");
      if (drawerEl) {
        drawerEl.textContent = `✓ Problem & Alternatives ready for ag/${clNum}`;
      }
      if (isVisible && !composerBusy) {
        renderDiffPane({ preserveScroll: true });
        showToast(`✓ Problem & Alternatives ready for ag/${clNum}!`);
      } else {
        pushAiAnalysisReadyNotification(clNum, data);
      }
      return;
    }
    if (isVisible && !composerBusy && !state.hiddenAiAnalysisCls.has(Number(clNum))) {
      renderDiffPane({ preserveScroll: true });
    }
  }

  function pushAiAnalysisReadyNotification(clNum, data) {
    const analysis = (data && data.analysis) || {};
    const scopeLabel = String(analysis.scope_label || "Intangibles ready");
    const notifKey = `ai-analysis-${clNum}`;
    state.replyNotifications = (state.replyNotifications || []).filter(
      n => String(n.itemId) !== notifKey
    );
    state.replyNotifications.push({
      id: `reply-b-${state.nextReplyNotifId++}`,
      kind: "ai_analysis_ready",
      clNum: Number(clNum),
      itemId: notifKey,
      locLabel: scopeLabel,
    });
    while (state.replyNotifications.length > MAX_REPLY_NOTIFICATION_BUBBLES) {
      state.replyNotifications.shift();
    }
    renderAgentReplyBubbleStack();
  }

  async function analyzeClIntangibles(clNum, force = false) {
    const num = Number(clNum);
    if (!num) return;
    const reqSeq = (state.aiAnalysisReqSeqByCl[num] = (state.aiAnalysisReqSeqByCl[num] || 0) + 1);
    state.inFlightAiAnalysisStartByCl[num] = true;
    const cObj = state.changes.find(x => Number(x.number) === num);
    const ps = Number((cObj && cObj.patch_set) || 1);
    state.hiddenAiAnalysisCls.delete(num);
    state.collapsedAiAnalysisCls.delete(num);
    state.aiAnalysisByCl[num] = {
      cl: String(num),
      patch_set: ps,
      analysis_status: "thinking",
      analysis: null,
      error: "",
    };
    syncAiAnalysisButton(getActiveCommitsForDiff());
    renderDiffPane({ preserveScroll: true });

    const drawerEl = document.getElementById("drawerStatus");
    if (drawerEl) {
      drawerEl.textContent = `Analyzing Problem & Alternatives on ag/${num}…`;
    }
    try {
      const projectId = await resolveSidecarProjectId();
      const resp = await sidecarFetch("/api/gerrit/ai-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "analyze",
          cl: num,
          ps,
          project: (cObj && cObj.project) || "",
          subject: (cObj && cObj.subject) || "",
          projectId,
          force: Boolean(force),
          viewer: state.viewer || "",
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error((data && data.error) || `HTTP ${resp.status}`);
      }
      if ((state.aiAnalysisReqSeqByCl[num] || 0) !== reqSeq) return;
      state.aiAnalysisByCl[num] = data;
      syncAiAnalysisButton(getActiveCommitsForDiff());
      renderDiffPane({ preserveScroll: true });
      ensureAiAnalysisPollingTimer();
    } catch (err) {
      if ((state.aiAnalysisReqSeqByCl[num] || 0) !== reqSeq) return;
      state.aiAnalysisByCl[num] = {
        cl: String(num),
        patch_set: ps,
        analysis_status: "error",
        analysis: null,
        error: String(err),
      };
      syncAiAnalysisButton(getActiveCommitsForDiff());
      renderDiffPane({ preserveScroll: true });
      showToast(`Problem & Alternatives analysis failed on ag/${num}: ${err}`);
    } finally {
      if ((state.aiAnalysisReqSeqByCl[num] || 0) === reqSeq) {
        delete state.inFlightAiAnalysisStartByCl[num];
      }
    }
  }

  async function cancelClAiAnalysis(clNum) {
    const num = Number(clNum);
    if (!num) return;
    state.aiAnalysisReqSeqByCl[num] = (state.aiAnalysisReqSeqByCl[num] || 0) + 1;
    delete state.inFlightAiAnalysisStartByCl[num];
    const cObj = state.changes.find(x => Number(x.number) === num);
    const ps = Number((cObj && cObj.patch_set) || 1);
    state.aiAnalysisByCl[num] = {
      cl: String(num),
      patch_set: ps,
      analysis_status: "not_analyzed",
      analysis: null,
      error: "",
    };
    syncAiAnalysisButton(getActiveCommitsForDiff());
    renderDiffPane({ preserveScroll: true });
    try {
      await sidecarFetch("/api/gerrit/ai-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "cancel", cl: num, ps }),
      });
      showToast(`Cancelled Problem & Alternatives analysis on ag/${num}.`);
    } catch (_err) {
      // Best-effort cancellation
    }
  }

  function hideAiAnalysisForCl(clNum) {
    const num = Number(clNum);
    state.hiddenAiAnalysisCls.add(num);
    syncAiAnalysisButton(getActiveCommitsForDiff());
    renderDiffPane({ preserveScroll: true });
  }

  function toggleAiAnalysisCollapse(clNum) {
    const num = Number(clNum);
    const panelEl = document.getElementById(`aiAnalysisPanel-${num}`);
    if (state.collapsedAiAnalysisCls.has(num)) {
      state.collapsedAiAnalysisCls.delete(num);
      if (panelEl) {
        panelEl.classList.remove("collapsed");
        const btn = panelEl.querySelector(".ai-collapse-btn");
        if (btn) btn.textContent = "Collapse ▴";
      }
      return;
    }
    state.collapsedAiAnalysisCls.add(num);
    if (panelEl) {
      panelEl.classList.add("collapsed");
      const btn = panelEl.querySelector(".ai-collapse-btn");
      if (btn) btn.textContent = "Expand ▾";
    }
  }

  function getThinkingAiAnalysisClNumbers() {
    return Object.keys(state.aiAnalysisByCl || {})
      .map(k => Number(k))
      .filter(num => {
        const entry = state.aiAnalysisByCl[num];
        return Boolean(num && entry && entry.analysis_status === "thinking");
      });
  }

  function ensureAiAnalysisPollingTimer() {
    if (window._aiAnalysisPollInterval) return;
    if (getThinkingAiAnalysisClNumbers().length === 0) return;
    window._aiAnalysisPollInterval = setInterval(() => {
      const thinkingNums = getThinkingAiAnalysisClNumbers();
      if (thinkingNums.length === 0) {
        clearInterval(window._aiAnalysisPollInterval);
        window._aiAnalysisPollInterval = null;
        return;
      }
      thinkingNums.forEach(num => {
        fetchAiAnalysisStatusForCl(num);
      });
    }, AI_ANALYSIS_POLL_INTERVAL_MS);
  }

  function renderAiAnalysisStatusBannerHtml(num, patchSet, status, errorMsg) {
    return window.AemuGerritReact.renderAiAnalysisStatusBannerHtml(num, patchSet, status, errorMsg);
  }

  function renderAiAlternativeCardHtml(num, alt) {
    /* aiAltCard-${num}-${altId} */
    const rawAltId = String(alt.id || "alt-1");
    const attachedItemsHtml = getInlineItemsForAiAlternative(num, rawAltId)
      .map(it => renderInlineCommentCardHtml(num, it))
      .join("");
    return window.AemuGerritReact.renderAiAlternativeCardHtml(
      num,
      alt,
      renderInlineMarkdownHtml(alt.how_it_works || ""),
      alt.tradeoff ? renderInlineMarkdownHtml(alt.tradeoff) : "",
      attachedItemsHtml
    );
  }

  function renderCommitAiAnalysisPanelHtml(c) {
    if (!c) return "";
    const num = Number(c.number);
    if (state.hiddenAiAnalysisCls.has(num)) return "";
    const entry = state.aiAnalysisByCl[num];
    const status = entry ? entry.analysis_status : "not_analyzed";
    if (status === "not_analyzed") return "";
    if (status === "thinking" || status === "error") {
      return renderAiAnalysisStatusBannerHtml(num, c.patch_set, status, entry && entry.error);
    }

    const analysis = entry.analysis;
    if (!analysis) return "";
    const isCollapsed = state.collapsedAiAnalysisCls.has(num);
    const tradeoffs = Array.isArray(analysis.key_tradeoffs) ? analysis.key_tradeoffs : [];
    const alts = Array.isArray(analysis.alternatives) ? analysis.alternatives : [];
    const analyzedByLdap = entry.analyzed_by ? String(entry.analyzed_by).split("@")[0] : "";

    return window.AemuGerritReact.renderCommitAiAnalysisPanelHtml({
      num,
      isCollapsed,
      scopeTone: analysis.scope_tone || "focus",
      scopeIcon: analysis.scope_icon || "🧭",
      scopeLabel: analysis.scope_label || "Design Assessment",
      analyzedByLdap,
      shared: Boolean(entry.shared),
      bottomLineHtml: analysis.bottom_line ? renderInlineMarkdownHtml(analysis.bottom_line) : "",
      underlyingProblemHtml: renderInlineMarkdownHtml(analysis.underlying_problem || ""),
      chosenSolutionHtml: renderInlineMarkdownHtml(analysis.chosen_solution || ""),
      tradeoffsHtmlList: tradeoffs.map(t => renderInlineMarkdownHtml(t)),
      alternativesCount: alts.length,
      alternativesCardsHtml: alts.map(alt => renderAiAlternativeCardHtml(num, alt)).join(""),
    });
  }

  function getInlineItemsForAiAlternative(clNum, altId) {
    const num = Number(clNum);
    const wantedAlt = String(altId || "");
    if (!wantedAlt) return [];
    const bundle = state.diffsByCl[num];
    if (bundle && Array.isArray(bundle.code_questions)) {
      mergeTranscriptCodeQuestions(bundle.code_questions, { silentDom: true, suppressNotifications: true });
    }
    const localList = state.inlineCommentsByCl[num] || [];
    localList.forEach(c => {
      if (!c.id) {
        c.id = `inline-${state.nextInlineItemId++}`;
      }
    });
    return localList.filter(c => String(c.alt_id || "") === wantedAlt);
  }

  function openAiAlternativeInComposer(clNum, altId, mode, overrideDraft = null) {
    const num = Number(clNum);
    const entry = state.aiAnalysisByCl[num];
    const alts = (entry && entry.analysis && Array.isArray(entry.analysis.alternatives))
      ? entry.analysis.alternatives
      : [];
    const alt = alts.find(a => String(a.id) === String(altId));
    if (!alt) return;

    if (state.hiddenAiAnalysisCls.has(num) || state.collapsedAiAnalysisCls.has(num)) {
      state.hiddenAiAnalysisCls.delete(num);
      state.collapsedAiAnalysisCls.delete(num);
      syncAiAnalysisButton(getActiveCommitsForDiff());
      renderDiffPane({ preserveScroll: true });
    }

    const altCardEl = document.getElementById(`aiAltCard-${num}-${alt.id}`);
    if (!altCardEl) return;

    document.querySelectorAll(".code-line.code-line-selected").forEach(
      el => el.classList.remove("code-line-selected")
    );

    const prevSel = state.codeSelection;
    const isSameAlt = Boolean(
      prevSel && Number(prevSel.clNum) === num && String(prevSel.altId || "") === String(alt.id)
    );
    if (!isSameAlt) {
      if (prevSel) saveSelectionDraftAsCollapsedItem(prevSel);
      collapseAllInlinePopups();
    }

    const resolvedMode = mode === "ask" ? "ask" : "comment";
    const defaultDraft = resolvedMode === "ask"
      ? String(alt.ask_prompt || "")
      : String(alt.review_comment_draft || "");
    const resolvedDraft = typeof overrideDraft === "string" && overrideDraft
      ? overrideDraft
      : defaultDraft;
    const altTitle = String(alt.title || alt.id);
    const snippetParts = [
      `[AI Analysis Alternative ${alt.id}: ${altTitle}]`,
      alt.how_it_works ? `How it works: ${alt.how_it_works}` : "",
      alt.tradeoff ? `Tradeoff: ${alt.tradeoff}` : "",
    ].filter(Boolean);

    state.codeSelection = {
      clNum: num,
      filePath: "/COMMIT_MSG",
      side: "REVISION",
      startLine: 1,
      endLine: 1,
      lineCount: 1,
      anchorIdx: null,
      startIdx: null,
      endIdx: null,
      altId: String(alt.id),
      altTitle,
      codeSnippet: snippetParts.join("\n"),
      mode: resolvedMode,
      draft: resolvedDraft,
      commentDraft: resolvedDraft,
      askDraft: resolvedDraft,
      unresolved: isSameAlt && prevSel.unresolved !== undefined ? prevSel.unresolved : true,
      sentPrompt: "",
      savedInlineId: null,
    };

    let insertAfterEl = altCardEl;
    while (
      insertAfterEl.nextElementSibling &&
      insertAfterEl.nextElementSibling.classList &&
      insertAfterEl.nextElementSibling.classList.contains("diff-inline-comment")
    ) {
      insertAfterEl = insertAfterEl.nextElementSibling;
    }
    mountCodeSelectionComposer(insertAfterEl, true);
  }

  function normalizeQuestionTurns(item) {
    const defaultAuthor = String((item && (item.author || item.author_name)) || "").split("@")[0].trim();
    if (item && Array.isArray(item.turns) && item.turns.length > 0) {
      return item.turns.map((t, idx) => {
        const reply = String(t.reply || "").trim();
        const turnAuthor = String(t.author || (idx === 0 ? defaultAuthor : "") || "").split("@")[0].trim();
        return {
          turn_index: idx,
          question: String(t.question || "").trim(),
          reply,
          status: reply ? "answered" : (t.status || "thinking"),
          author: turnAuthor,
          author_email: String(t.author_email || "").trim(),
          created_at: String(t.created_at || "").trim(),
          conversation_id: String(t.conversation_id || "").trim(),
        };
      });
    }
    const fallbackReply = String((item && item.reply) || "").trim();
    return [{
      turn_index: 0,
      question: String((item && item.message) || "").trim(),
      reply: fallbackReply,
      status: fallbackReply ? "answered" : ((item && item.reply_status) || "thinking"),
      author: defaultAuthor,
      author_email: "",
      created_at: "",
      conversation_id: String((item && item.conversation_id) || "").trim(),
    }];
  }

  function isQuestionThreadPending(item) {
    if (!item || item.kind !== "agent_question") return false;
    if (item.sending) return true;
    const turns = normalizeQuestionTurns(item);
    const last = turns[turns.length - 1];
    return !Boolean(last && String(last.reply || "").trim());
  }

  function formatFunctionLineLabel(functionName, filePath, lineNum) {
    const ln = Math.max(1, Number(lineNum || 1));
    const fn = String(functionName || "").trim();
    if (fn) return `${fn}:${ln}`;
    if (!filePath || filePath === "/COMMIT_MSG") return `COMMIT_MSG:${ln}`;
    const parts = String(filePath).split("/");
    const base = parts[parts.length - 1] || filePath;
    return `${base}:${ln}`;
  }

  function renderAgentReplyBubbleStack() {
    const stackEl = document.getElementById("agentReplyBubbleStack");
    if (!stackEl) return;
    const list = state.replyNotifications || [];
    if (list.length === 0) {
      stackEl.innerHTML = "";
      return;
    }
    stackEl.innerHTML = list.map(notif => {
      const safeId = escapeHtml(notif.id);
      if (notif.kind === "ai_analysis_ready") {
        return `<div class="agent-reply-bubble" data-notif-id="${safeId}" onclick="navigateToAgentReplyBubble('${safeId}')" title="Click to view Problem &amp; Alternatives on ag/${notif.clNum}">
          <span class="agent-reply-bubble-icon">🧠</span>
          <div class="agent-reply-bubble-text">
            <div class="agent-reply-bubble-title">Problem &amp; Alternatives ready (<code>${escapeHtml(notif.locLabel)}</code>)</div>
            <div class="agent-reply-bubble-sub">ag/${notif.clNum} · Click to view analysis</div>
          </div>
          <button class="agent-reply-bubble-close" onclick="event.stopPropagation(); dismissAgentReplyBubble('${safeId}')" title="Dismiss notification">✕</button>
        </div>`;
      }
      const shortFile = notif.filePath === "/COMMIT_MSG"
        ? "COMMIT_MSG"
        : String(notif.filePath || "").split("/").slice(-2).join("/");
      return `<div class="agent-reply-bubble" data-notif-id="${safeId}" onclick="navigateToAgentReplyBubble('${safeId}')" title="Click to jump to ${escapeHtml(notif.filePath || "")}:L${notif.startLine} on ag/${notif.clNum}">
        <span class="agent-reply-bubble-icon">✨</span>
        <div class="agent-reply-bubble-text">
          <div class="agent-reply-bubble-title">Agent responded at <code>${escapeHtml(notif.locLabel)}</code></div>
          <div class="agent-reply-bubble-sub">ag/${notif.clNum} · ${escapeHtml(shortFile)}</div>
        </div>
        <button class="agent-reply-bubble-close" onclick="event.stopPropagation(); dismissAgentReplyBubble('${safeId}')" title="Dismiss notification">✕</button>
      </div>`;
    }).join("");
  }

  function pushAgentReplyNotification(info) {
    if (!info || !info.clNum) return;
    const locLabel = formatFunctionLineLabel(info.functionName, info.filePath, info.startLine);
    const existingIdx = state.replyNotifications.findIndex(
      n => String(n.itemId) === String(info.itemId) && Number(n.clNum) === Number(info.clNum)
    );
    if (existingIdx >= 0) {
      state.replyNotifications.splice(existingIdx, 1);
    }
    state.replyNotifications.push({
      id: `reply-b-${state.nextReplyNotifId++}`,
      clNum: Number(info.clNum),
      itemId: String(info.itemId || ""),
      conversationId: String(info.conversationId || ""),
      filePath: String(info.filePath || ""),
      startLine: Math.max(1, Number(info.startLine || 1)),
      functionName: String(info.functionName || ""),
      locLabel,
    });
    while (state.replyNotifications.length > MAX_REPLY_NOTIFICATION_BUBBLES) {
      state.replyNotifications.shift();
    }
    renderAgentReplyBubbleStack();
  }

  function dismissAgentReplyBubble(notifId) {
    state.replyNotifications = (state.replyNotifications || []).filter(
      n => String(n.id) !== String(notifId)
    );
    renderAgentReplyBubbleStack();
  }

  async function navigateToAgentReplyBubble(notifId) {
    const notif = (state.replyNotifications || []).find(n => String(n.id) === String(notifId));
    dismissAgentReplyBubble(notifId);
    if (!notif) return;

    if (notif.kind === "ai_analysis_ready") {
      const clNum = Number(notif.clNum);
      state.hiddenAiAnalysisCls.delete(clNum);
      state.collapsedAiAnalysisCls.delete(clNum);
      const activeNums = getActiveCommitsForDiff().map(c => Number(c.number));
      if (!activeNums.includes(clNum)) {
        selectCl(clNum, true);
        await ensureActiveDiffsLoaded({ preserveScroll: true });
      } else {
        syncAiAnalysisButton(getActiveCommitsForDiff());
        renderDiffPane({ preserveScroll: true });
      }
      const panelEl = document.getElementById(`aiAnalysisPanel-${clNum}`);
      if (panelEl) {
        panelEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      return;
    }

    const activeNums = getActiveCommitsForDiff().map(c => Number(c.number));
    if (!activeNums.includes(Number(notif.clNum))) {
      selectCl(Number(notif.clNum), true);
      await ensureActiveDiffsLoaded({ preserveScroll: true });
    }

    const list = state.inlineCommentsByCl[notif.clNum] || [];
    const item = list.find(
      it =>
        String(it.id) === String(notif.itemId) ||
        (notif.conversationId && String(it.conversation_id || "") === String(notif.conversationId))
    );
    if (item && item.alt_id) {
      const nCl = Number(notif.clNum);
      if (state.hiddenAiAnalysisCls.has(nCl) || state.collapsedAiAnalysisCls.has(nCl)) {
        state.hiddenAiAnalysisCls.delete(nCl);
        state.collapsedAiAnalysisCls.delete(nCl);
        syncAiAnalysisButton(getActiveCommitsForDiff());
        renderDiffPane({ preserveScroll: true });
      }
    }
    if (item) {
      item.collapsed = false;
      insertOrReplaceInlineItemInDom(notif.clNum, item, { forceRender: true });
    }

    const cardEl = document.getElementById(`inlineItem-${item ? item.id : notif.itemId}`);
    if (!cardEl) return;
    cardEl.classList.remove("collapsed");
    const collapseBtn = cardEl.querySelector(".inline-collapse-btn");
    if (collapseBtn) collapseBtn.textContent = "Hide ▴";
    cardEl.scrollIntoView({ behavior: "smooth", block: "center" });
    cardEl.classList.remove("qa-flash-target");
    void cardEl.offsetWidth;
    cardEl.classList.add("qa-flash-target");
    setTimeout(() => cardEl.classList.remove("qa-flash-target"), QA_FLASH_DURATION_MS);
  }

  function buildIncomingTurnsFromQuestionPayload(q, fallbackQuestion, fallbackReply, fallbackStatus) {
    const defaultAuthor = String((q && q.author) || "").split("@")[0].trim();
    if (Array.isArray(q.turns) && q.turns.length > 0) {
      return q.turns.map((t, idx) => {
        const rep = String(t.reply || "").trim();
        const turnAuthor = String(t.author || (idx === 0 ? defaultAuthor : "") || "").split("@")[0].trim();
        return {
          turn_index: idx,
          question: String(t.question || "").trim(),
          reply: rep,
          status: rep ? "answered" : (t.status || "thinking"),
          author: turnAuthor,
          author_email: String(t.author_email || "").trim(),
          created_at: String(t.created_at || "").trim(),
          conversation_id: String(t.conversation_id || "").trim(),
        };
      });
    }
    return [{
      turn_index: 0,
      question: fallbackQuestion,
      reply: fallbackReply,
      status: fallbackStatus,
      author: defaultAuthor,
      author_email: String((q && q.author_email) || "").trim(),
      created_at: "",
      conversation_id: String((q && q.conversation_id) || "").trim(),
    }];
  }

  function findMatchingInlineQuestionItem(list, q, qFile, qStart, qEnd, qInitialMsg) {
    return list.find(item => {
      if (item.kind !== "agent_question") return false;
      if (q.thread_id && (String(item.thread_id || "") === String(q.thread_id) || String(item.id) === String(q.thread_id))) {
        return true;
      }
      if (q.id && String(item.id) === String(q.id)) return true;
      if (item.conversation_id && q.conversation_id && String(item.conversation_id) === String(q.conversation_id)) {
        return true;
      }
      if (item.conversation_id && q.conversation_id) return false;
      return (
        (item.file_path || "") === qFile &&
        Number(item.start_line || item.line || 1) === qStart &&
        Number(item.end_line || item.line || 1) === qEnd &&
        String(item.initial_question || item.message || "").trim() === qInitialMsg
      );
    });
  }

  function mergeSingleQuestionIntoClList(num, list, q, opts) {
    const qFile = q.file_path || "/COMMIT_MSG";
    const qStart = Number(q.start_line || q.line || 1);
    const qEnd = Number(q.end_line || q.line || qStart);
    const qMsg = String(q.question || "").trim();
    const qInitial = String(q.initial_question || (q.turns && q.turns[0] && q.turns[0].question) || qMsg).trim();
    const qReply = String(q.reply || "").trim();
    const qStatus = q.status || (qReply ? "answered" : "thinking");
    const incomingTurns = buildIncomingTurnsFromQuestionPayload(q, qMsg, qReply, qStatus);
    const existing = findMatchingInlineQuestionItem(list, q, qFile, qStart, qEnd, qInitial);
    const resolvedAuthor = String(q.author || (incomingTurns[0] && incomingTurns[0].author) || (state.viewer || "you")).split("@")[0].trim();
    const incomingParticipants = Array.isArray(q.participants) ? q.participants : (resolvedAuthor ? [resolvedAuthor] : []);

    if (!existing) {
      const latest = incomingTurns[incomingTurns.length - 1];
      const altMatch = String(q.code_snippet || "").match(/^\[(?:AI Analysis|Problem & Alternatives) Alternative (alt-\d+):\s*([^\]]*)\]/);
      const newItem = {
        id: q.thread_id || q.id || `inline-${state.nextInlineItemId++}`,
        thread_id: q.thread_id || q.id || "",
        conversation_id: q.conversation_id || "",
        kind: "agent_question",
        author: resolvedAuthor,
        author_name: resolvedAuthor,
        participants: incomingParticipants,
        is_shared: Boolean(q.is_shared),
        file_path: qFile,
        line: qEnd,
        start_line: qStart,
        end_line: qEnd,
        side: q.side || "REVISION",
        alt_id: altMatch ? altMatch[1] : "",
        alt_title: altMatch ? altMatch[2].trim() : "",
        function_name: q.function_name || (altMatch ? altMatch[2].trim() : ""),
        initial_question: qInitial,
        message: latest.question,
        reply: latest.reply,
        reply_status: latest.status,
        turns: incomingTurns,
        code_snippet: q.code_snippet || "",
        sending: false,
        dispatched: true,
        collapsed: false,
      };
      list.push(newItem);
      if (!opts.silentDom) insertOrReplaceInlineItemInDom(num, newItem);
      return true;
    }

    const prevTurns = normalizeQuestionTurns(existing);
    const prevAnswered = prevTurns.filter(t => Boolean(String(t.reply || "").trim())).length;
    const mergedTurns = incomingTurns.slice();
    if (prevTurns.length > mergedTurns.length && !String(prevTurns[prevTurns.length - 1].reply || "").trim()) {
      mergedTurns.push(...prevTurns.slice(mergedTurns.length));
    }
    const latestTurn = mergedTurns[mergedTurns.length - 1];
    const nextAnswered = mergedTurns.filter(t => Boolean(String(t.reply || "").trim())).length;
    const prevJson = JSON.stringify(prevTurns);
    const nextJson = JSON.stringify(mergedTurns);
    const metaChanged = (
      Boolean(q.is_shared) !== Boolean(existing.is_shared) ||
      JSON.stringify(incomingParticipants) !== JSON.stringify(existing.participants || [])
    );
    const didChange = prevJson !== nextJson || metaChanged || Boolean(existing.sending) || (q.function_name && !existing.function_name);
    if (!didChange) return false;

    if (q.thread_id) existing.thread_id = q.thread_id;
    if (q.conversation_id) existing.conversation_id = q.conversation_id;
    if (q.function_name) existing.function_name = q.function_name;
    if (resolvedAuthor) {
      existing.author = resolvedAuthor;
      existing.author_name = resolvedAuthor;
    }
    existing.participants = incomingParticipants;
    existing.is_shared = Boolean(q.is_shared || existing.is_shared);
    existing.turns = mergedTurns;
    existing.initial_question = existing.initial_question || mergedTurns[0].question;
    existing.message = latestTurn.question;
    existing.reply = latestTurn.reply;
    existing.reply_status = latestTurn.status;
    existing.sending = false;
    existing.dispatched = true;

    const newlyAnswered = nextAnswered > prevAnswered && Boolean(latestTurn.reply);
    if (newlyAnswered) {
      delete state.editingQaItemById[existing.id];
    }
    if (!opts.silentDom) {
      insertOrReplaceInlineItemInDom(num, existing, { forceRender: newlyAnswered });
    }
    if (newlyAnswered && !opts.suppressNotifications) {
      const fnName = existing.function_name || resolveEnclosingSymbolForDiffLine(
        num,
        existing.file_path || qFile,
        existing.start_idx,
        existing.start_line || qStart,
        existing.side || "REVISION"
      );
      existing.function_name = fnName;
      pushAgentReplyNotification({
        clNum: num,
        itemId: existing.id,
        conversationId: existing.conversation_id || "",
        filePath: existing.file_path || qFile,
        startLine: existing.start_line || qStart,
        functionName: fnName,
      });
    }
    return true;
  }

  function mergeTranscriptCodeQuestions(questions, opts = {}) {
    if (!Array.isArray(questions) || questions.length === 0) return false;
    let changed = false;
    questions.forEach(q => {
      const num = Number(q.cl);
      if (!num) return;
      if (!state.inlineCommentsByCl[num]) {
        state.inlineCommentsByCl[num] = [];
      }
      if (mergeSingleQuestionIntoClList(num, state.inlineCommentsByCl[num], q, opts)) {
        changed = true;
      }
    });
    return changed;
  }

  function collectClNumbersForQuestionPolling(activeCommits) {
    const commits = Array.isArray(activeCommits) ? activeCommits : getActiveCommitsForDiff();
    const hasInlineMap = hasAnyOwnProp(state.inlineCommentsByCl);
    if (!hasInlineMap && commits.length === 1) {
      const singleNum = Number(commits[0] && commits[0].number);
      return singleNum > 0 ? [singleNum] : _PG_EMPTY_ARR;
    }
    const nums = new Set();
    for (let i = 0; i < commits.length; i++) {
      const num = Number(commits[i] && commits[i].number);
      if (num > 0) nums.add(num);
    }
    if (hasInlineMap) {
      const clKeys = Object.keys(state.inlineCommentsByCl);
      for (let i = 0; i < clKeys.length; i++) {
        const clKey = clKeys[i];
        const list = state.inlineCommentsByCl[clKey] || _PG_EMPTY_ARR;
        if (list.some(it => isQuestionThreadPending(it))) {
          const kNum = Number(clKey);
          if (kNum > 0) nums.add(kNum);
        }
      }
    }
    return nums.size > 0 ? Array.from(nums) : _PG_EMPTY_ARR;
  }

  let _inFlightQuestionPollSig = "";
  let _lastQuestionPollSig = "";
  let _lastQuestionPollTs = 0;

  async function pollTranscriptCodeQuestions(activeCommits, opts) {
    if (window.__GERRIT_UI_BOOTSTRAP__ && window.__GERRIT_UI_BOOTSTRAP__.changes) {
      return;
    }
    const clNums = collectClNumbersForQuestionPolling(activeCommits);
    if (clNums.length === 0) return;
    const convId = (window.sidecar && window.sidecar.conversationId) || "";
    const clsJoined = clNums.length === 1 ? String(clNums[0]) : clNums.join(",");
    const pollSig = convId ? `${clsJoined}|${convId}` : clsJoined;
    if (_inFlightQuestionPollSig === pollSig) return;
    const throttleMs = (opts && opts.throttleMs) || 0;
    if (throttleMs > 0 && _lastQuestionPollSig === pollSig && (performance.now() - _lastQuestionPollTs) < throttleMs) {
      return;
    }
    _inFlightQuestionPollSig = pollSig;
    try {
      const qs = `?cls=${encodeURIComponent(clsJoined)}${convId ? `&conversationId=${encodeURIComponent(convId)}` : ""}`;
      const resp = await sidecarFetch(`/api/gerrit/code-questions${qs}`);
      _lastQuestionPollSig = pollSig;
      _lastQuestionPollTs = performance.now();
      if (!resp.ok) return;
      const data = await resp.json();
      if (Array.isArray(data.questions)) {
        mergeTranscriptCodeQuestions(data.questions, { silentDom: false, suppressNotifications: false });
      }
    } catch (_err) {
      // Ignore transient poll errors
    } finally {
      if (_inFlightQuestionPollSig === pollSig) {
        _inFlightQuestionPollSig = "";
      }
    }
  }

  function hasPendingCodeQuestions() {
    for (const clKey of Object.keys(state.inlineCommentsByCl || {})) {
      const list = state.inlineCommentsByCl[clKey] || [];
      if (list.some(it => isQuestionThreadPending(it))) {
        return true;
      }
    }
    return false;
  }

  function ensureCodeQuestionPollingTimer() {
    if (window._codeQuestionPollTimer) return;
    window._codeQuestionPollTick = 0;
    window._codeQuestionPollTimer = setInterval(() => {
      window._codeQuestionPollTick = (window._codeQuestionPollTick || 0) + 1;
      if (hasPendingCodeQuestions() || (window._codeQuestionPollTick % 3 === 0 && getActiveCommitsForDiff().length > 0)) {
        pollTranscriptCodeQuestions();
      }
    }, CODE_QUESTION_POLL_INTERVAL_MS);
  }

  function isPgRobotComment(c) {
    if (!c) return false;
    if (c.is_robot === true || c.is_robot === "true" || c.is_robot === 1) return true;
    if (c.robot_id && String(c.robot_id).trim()) return true;
    if (Array.isArray(c.fix_suggestions) && c.fix_suggestions.length > 0) return true;
    const author = String(c.author_name || c.root_author_name || "").toLowerCase();
    const email = String(c.author_email || "").toLowerCase();
    for (const tok of ["robot", "treehugger", "automerger", "presubmit", "tricorder", "clang-tidy", "android-build"]) {
      if (author.includes(tok) || email.includes(tok)) return true;
    }
    if (c.raw_thread && (c.raw_thread.is_robot || c.raw_thread.robot_id)) return true;
    if (Array.isArray(c.messages)) {
      return c.messages.some(m => m && (m.is_robot || m.robot_id || (Array.isArray(m.fix_suggestions) && m.fix_suggestions.length > 0)));
    }
    return false;
  }

  function renderPgFixSuggestionsHtml(fixSuggestions, safeId, num) {
    if (!Array.isArray(fixSuggestions) || fixSuggestions.length === 0) return "";
    return fixSuggestions.map((fix, fIdx) => {
      const fixId = `${safeId}-fix-${fIdx}`;
      const desc = fix.description || "Code replacement suggestion";
      let replacementCode = "";
      if (typeof fix.replacement === "string") {
        replacementCode = fix.replacement;
      } else if (Array.isArray(fix.replacements) && fix.replacements.length > 0) {
        replacementCode = fix.replacements.map(r => r && (r.replacement != null ? r.replacement : "")).join("\n");
      }
      return `<div class="pg-fix-suggestion-card" id="pgFixCard-${fixId}">
        <div class="pg-fix-suggestion-header">
          <span class="pg-fix-icon" aria-hidden="true">💡</span>
          <span class="pg-fix-title">Suggested Fix: ${escapeHtml(desc)}</span>
          <div style="margin-left:auto;display:flex;align-items:center;gap:6px;">
            <button type="button" class="commit-msg-btn pg-btn-copy-fix" onclick="event.stopPropagation(); copyPgFixCode('${fixId}', this)" title="Copy suggested fix to clipboard">📋 Copy Fix</button>
            <button type="button" class="commit-msg-btn pg-btn-apply-fix" onclick="event.stopPropagation(); applyPgFixToReply('${safeId}', ${num}, '${fixId}')" title="Insert suggested fix into reply composer">💬 Apply to Reply</button>
          </div>
        </div>
        <div class="pg-fix-body">
          <pre class="pg-fix-snippet" id="pgFixCode-${fixId}"><code>${escapeHtml(replacementCode)}</code></pre>
        </div>
      </div>`;
    }).join("");
  }

  async function copyPgFixCode(fixId, btnEl) {
    const el = document.getElementById(`pgFixCode-${fixId}`);
    const code = el ? el.textContent : "";
    if (!code) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(code);
      }
    } catch (_) {}
    if (btnEl) {
      const orig = btnEl.textContent;
      btnEl.textContent = "✓ Copied";
      btnEl.classList.add("is-copied");
      setTimeout(() => {
        btnEl.textContent = orig;
        btnEl.classList.remove("is-copied");
      }, 1800);
    }
    if (typeof showToast === "function") {
      showToast("✓ Copied suggested fix to clipboard");
    }
  }

  function applyPgFixToReply(safeId, num, fixId) {
    const el = document.getElementById(`pgFixCode-${fixId}`);
    const code = el ? el.textContent : "";
    if (typeof openInlineReplyComposer === "function") {
      openInlineReplyComposer(num, safeId);
    }
    const input = document.getElementById(`commentReplyInput-${safeId}`);
    if (input) {
      const prefix = input.value.trim() ? input.value + "\n\n" : "";
      input.value = `${prefix}\`\`\`suggestion\n${code}\n\`\`\`\n`;
      if (typeof onInlineReplyDraftInput === "function") {
        onInlineReplyDraftInput(safeId, input.value);
      }
      input.focus();
    }
    if (typeof showToast === "function") {
      showToast("✓ Applied suggested fix to reply composer");
    }
  }

  function buildServerThreadInlineItem(num, t, tIdx) {
    const itemId = t._ui_item_id || `srv-thread-${num}-${tIdx}`;
    t._ui_item_id = itemId;
    const msgs = Array.isArray(t.messages) ? t.messages : [];
    const latestObj = msgs.length > 0 ? msgs[msgs.length - 1] : null;
    const isRobot = Boolean(t.is_robot || (latestObj && latestObj.is_robot) || t.robot_id || (latestObj && latestObj.robot_id));
    const robotId = t.robot_id || (latestObj && latestObj.robot_id) || "";
    const robotRunId = t.robot_run_id || (latestObj && latestObj.robot_run_id) || "";
    const url = t.url || (latestObj && latestObj.url) || "";
    const fixSuggestions = (Array.isArray(t.fix_suggestions) && t.fix_suggestions.length > 0)
      ? t.fix_suggestions
      : ((latestObj && Array.isArray(latestObj.fix_suggestions)) ? latestObj.fix_suggestions : []);

    return {
      id: itemId,
      kind: "comment",
      comment_id: String(t.leaf_id || (latestObj && latestObj.comment_id) || t.root_id || ""),
      root_id: String(t.root_id || ""),
      leaf_id: String(t.leaf_id || (latestObj && latestObj.comment_id) || ""),
      server_id: String(t.id || ""),
      patch_set: Number(t.patch_set || (latestObj && latestObj.patch_set) || 0) || null,
      author_name: (latestObj && latestObj.author_name) || t.author_name || "reviewer",
      root_author_name: t.author_name || "reviewer",
      file_path: t.file_path,
      line: Number(t.line || 1),
      start_line: Number(t.start_line || t.line || 1),
      end_line: Number(t.end_line || t.line || 1),
      side: t.side || "REVISION",
      unresolved: t.unresolved !== false,
      message: t.latest_message || (latestObj && latestObj.message) || "",
      messages: msgs,
      collapsed: state.collapsedInlineIds.has(itemId),
      raw_thread: t,
      is_robot: isRobot,
      robot_id: robotId,
      robot_run_id: robotRunId,
      url: url,
      fix_suggestions: fixSuggestions,
    };
  }

  function resolveBundleChangeObj(bundle, num) {
    if (bundle && bundle._cachedChangeRefArr === state.changes && bundle._cachedChangeClNum === num) {
      return bundle._cachedChangeObjRef;
    }
    const c = findPgChangeByNumber(num);
    if (bundle) {
      bundle._cachedChangeRefArr = state.changes;
      bundle._cachedChangeClNum = num;
      bundle._cachedChangeObjRef = c || null;
    }
    return c;
  }

  function getInlineCommentsForFile(clNum, filePath) {
    const num = +clNum;
    const bundle = state.diffsByCl[num];
    const localList = (state.inlineCommentsByCl && state.inlineCommentsByCl[num]) || _PG_EMPTY_ARRAY;
    const ibSub = state._inlineByFileSub;
    if (
      ibSub &&
      ibSub.isEmpty &&
      ibSub.num === num &&
      ibSub.bundleRef === bundle &&
      ibSub.locRef === localList &&
      localList.length === 0 &&
      (!bundle || (!bundle.comment_threads && !bundle.unresolved_threads && (!bundle.code_questions || bundle.code_questions.length === 0))) &&
      (!ibSub.changeRef || (!ibSub.changeRef.comment_threads && !ibSub.changeRef.unresolved_threads))
    ) {
      return _PG_EMPTY_ARRAY;
    }
    let serverThreads = bundle && (bundle.comment_threads || bundle.unresolved_threads);
    let cObj = null;
    if (!serverThreads) {
      cObj = resolveBundleChangeObj(bundle, num);
      serverThreads = (cObj && (cObj.comment_threads || cObj.unresolved_threads)) || _PG_EMPTY_ARRAY;
    }
    if (bundle && Array.isArray(bundle.code_questions) && bundle.code_questions.length > 0) {
      mergeTranscriptCodeQuestions(bundle.code_questions, { silentDom: true, suppressNotifications: true });
    }
    if (serverThreads.length === 0 && localList.length === 0) {
      state._inlineByFileSub = {
        isEmpty: true,
        num,
        bundleRef: bundle,
        changeRef: cObj,
        locRef: localList,
      };
      return _PG_EMPTY_ARRAY;
    }
    const collapsedSize = (serverThreads.length > 0 && state.collapsedInlineIds) ? state.collapsedInlineIds.size : 0;
    if (
      ibSub &&
      !ibSub.isEmpty &&
      ibSub.num === num &&
      ibSub.srvRef === serverThreads &&
      ibSub.srvLen === serverThreads.length &&
      ibSub.locRef === localList &&
      ibSub.locLen === localList.length &&
      ibSub.collapsedSize === collapsedSize &&
      serverThreads.length <= 1 &&
      localList.length <= 1 &&
      collapsedSize === 0 &&
      state._cachedInlineByFileMap !== null
    ) {
      const s0 = serverThreads[0];
      const l0 = localList[0];
      if (
        (!s0 || (ibSub.s0Id === (s0.id || s0.root_id || 0) && ibSub.s0Fp === (s0.file_path || "") && ibSub.s0Unres === (s0.unresolved !== false))) &&
        (!l0 || (ibSub.l0Id === (l0.id || "") && ibSub.l0Alt === (l0.alt_id || "") && ibSub.l0Fp === (l0.file_path || "")))
      ) {
        return state._cachedInlineByFileMap.get(filePath) || _PG_EMPTY_ARRAY;
      }
    }
    if (localList.length <= 64 && serverThreads.length <= 64) {
      let localSig = String(localList.length);
      for (let i = 0; i < localList.length; i++) {
        const lc = localList[i];
        if (!lc) continue;
        if (!lc.id && !lc.alt_id) {
          lc.id = `inline-${state.nextInlineItemId++}`;
        }
        localSig += `:${lc.id || ""}:${lc.alt_id || ""}:${lc.file_path || ""}`;
      }
      let srvSig = String(serverThreads.length);
      for (let tIdx = 0; tIdx < serverThreads.length; tIdx++) {
        const st = serverThreads[tIdx];
        if (!st) continue;
        srvSig += `:${st.id || st.root_id || tIdx}:${st.file_path || ""}:${st.unresolved === false ? 0 : 1}`;
      }
      const collapsedSig = collapsedSize > 0
        ? Array.from(state.collapsedInlineIds).join(",")
        : "";
      const groupKey = `${num}|${srvSig}|${localSig}|${collapsedSig}`;
      if (
        state._cachedInlineByFileKey === groupKey &&
        state._cachedInlineByFileThreadsRef === serverThreads &&
        state._cachedInlineByFileLocalRef === localList &&
        state._cachedInlineByFileMap !== null
      ) {
        if (serverThreads.length <= 1 && localList.length <= 1 && collapsedSize === 0) {
          const s0 = serverThreads[0];
          const l0 = localList[0];
          state._inlineByFileSub = {
            num, srvRef: serverThreads, srvLen: serverThreads.length, locRef: localList, locLen: localList.length, collapsedSize: 0,
            s0Id: s0 ? (s0.id || s0.root_id || 0) : null, s0Fp: s0 ? (s0.file_path || "") : "", s0Unres: s0 ? (s0.unresolved !== false) : false,
            l0Id: l0 ? (l0.id || "") : "", l0Alt: l0 ? (l0.alt_id || "") : "", l0Fp: l0 ? (l0.file_path || "") : ""
          };
        }
        return state._cachedInlineByFileMap.get(filePath) || _PG_EMPTY_ARRAY;
      }
      const byFile = new Map();
      for (let tIdx = 0; tIdx < serverThreads.length; tIdx++) {
        const t = serverThreads[tIdx];
        if (!t) continue;
        const fp = t.file_path || "";
        let arr = byFile.get(fp);
        if (!arr) {
          arr = [];
          byFile.set(fp, arr);
        }
        arr.push(buildServerThreadInlineItem(num, t, tIdx));
      }
      for (let i = 0; i < localList.length; i++) {
        const c = localList[i];
        if (!c || c.alt_id) continue;
        const fp = c.file_path || "";
        let arr = byFile.get(fp);
        if (!arr) {
          arr = [];
          byFile.set(fp, arr);
        }
        arr.push(c);
      }
      state._cachedInlineByFileKey = groupKey;
      state._cachedInlineByFileThreadsRef = serverThreads;
      state._cachedInlineByFileLocalRef = localList;
      state._cachedInlineByFileMap = byFile;
      if (serverThreads.length <= 1 && localList.length <= 1 && collapsedSize === 0) {
        const s0 = serverThreads[0];
        const l0 = localList[0];
        state._inlineByFileSub = {
          num, srvRef: serverThreads, srvLen: serverThreads.length, locRef: localList, locLen: localList.length, collapsedSize: 0,
          s0Id: s0 ? (s0.id || s0.root_id || 0) : null, s0Fp: s0 ? (s0.file_path || "") : "", s0Unres: s0 ? (s0.unresolved !== false) : false,
          l0Id: l0 ? (l0.id || "") : "", l0Alt: l0 ? (l0.alt_id || "") : "", l0Fp: l0 ? (l0.file_path || "") : ""
        };
      } else {
        state._inlineByFileSub = null;
      }
      return byFile.get(filePath) || _PG_EMPTY_ARRAY;
    }
    const list = [];
    for (let tIdx = 0; tIdx < serverThreads.length; tIdx++) {
      const t = serverThreads[tIdx];
      if ((t.file_path || "") === filePath) {
        list.push(buildServerThreadInlineItem(num, t, tIdx));
      }
    }
    for (let i = 0; i < localList.length; i++) {
      const c = localList[i];
      if (c.alt_id) continue;
      if ((c.file_path || "") === filePath) {
        if (!c.id) {
          c.id = `inline-${state.nextInlineItemId++}`;
        }
        list.push(c);
      }
    }
    return list;
  }

  function renderAgentReplyMarkdownHtml(rawReply) {
    let text = String(rawReply || "").trim();
    if (!text) return "";
    // If an earlier turn included multiple sections separated by ---, prefer the section answering the code question
    const answerSplit = text.match(/(?:^|\n)---\s*\n+([\s\S]*Answer to Your Sidecar Code Question[\s\S]*)$/i);
    if (answerSplit) {
      text = answerSplit[1].trim();
    }
    const lines = text.split(/\r?\n/);
    const blocks = [];
    let i = 0;
    let para = [];
    const flushPara = () => {
      if (para.length > 0) {
        blocks.push(`<p>${para.map(ln => renderInlineMarkdownHtml(ln, false)).join("<br/>")}</p>`);
        para = [];
      }
    };
    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!trimmed) {
        flushPara();
        i++;
        continue;
      }
      const fenceMatch = trimmed.match(/^(`{3,}|~{3,})(.*)$/);
      if (fenceMatch) {
        flushPara();
        const fence = fenceMatch[1];
        const lang = fenceMatch[2].trim();
        const codeLines = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence)) {
          codeLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++;
        const langHtml = lang ? `<div class="commit-md-pre-lang">${escapeHtml(lang)}</div>` : "";
        blocks.push(`<pre class="commit-md-pre">${langHtml}<code class="commit-md-code-block">${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        continue;
      }
      const hMatch = line.match(/^\s*(#{1,4})\s+(.+)$/);
      if (hMatch) {
        flushPara();
        const lvl = Math.min(4, hMatch[1].length);
        blocks.push(`<div class="commit-md-h commit-md-h${lvl}">${renderInlineMarkdownHtml(hMatch[2].trim(), false)}</div>`);
        i++;
        continue;
      }
      const listMatch = line.match(/^(\s*)([-*+•]|\d+\.)\s+(.+)$/);
      if (listMatch) {
        flushPara();
        const isOrd = /\d+\./.test(listMatch[2]);
        const tag = isOrd ? "ol" : "ul";
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(/^(\s*)([-*+•]|\d+\.)\s+(.+)$/);
          if (!m) break;
          const indentPx = Math.min(36, Math.floor(m[1].length / 2) * 12);
          items.push(`<li class="commit-md-li" ${indentPx ? `style="margin-left:${indentPx}px"` : ""}>${renderInlineMarkdownHtml(m[3], false)}</li>`);
          i++;
        }
        blocks.push(`<${tag} class="commit-md-list">${items.join("")}</${tag}>`);
        continue;
      }
      para.push(line);
      i++;
    }
    flushPara();
    return blocks.join("");
  }

  function collectQuestionParticipants(c, turns) {
    const seen = [];
    const addAuthor = raw => {
      const ldap = String(raw || "").split("@")[0].trim();
      if (ldap && !seen.includes(ldap)) seen.push(ldap);
    };
    if (c && Array.isArray(c.participants)) c.participants.forEach(addAuthor);
    if (Array.isArray(turns)) turns.forEach(t => addAuthor(t && t.author));
    if (seen.length === 0 && c) addAuthor(c.author || c.author_name);
    return seen;
  }

  function renderQuestionTurnAuthorChipHtml(turn, fallbackAuthor) {
    const authorLdap = String((turn && turn.author) || fallbackAuthor || "").split("@")[0].trim();
    if (!authorLdap) return "";
    const viewerLdap = String(state.viewer || "").split("@")[0].trim();
    const isTeammate = Boolean(viewerLdap && authorLdap.toLowerCase() !== viewerLdap.toLowerCase());
    return `<span class="qa-turn-author-chip${isTeammate ? " is-teammate" : ""}" title="Asked by @${escapeHtml(authorLdap)}">@${escapeHtml(authorLdap)}</span>`;
  }

  function renderQuestionGroupBadgeHtml(c, turns) {
    const participants = collectQuestionParticipants(c, turns);
    const viewerLdap = String(state.viewer || "").split("@")[0].trim();
    if (participants.length > 1) {
      const listStr = participants.map(p => `@${escapeHtml(p)}`).join(", ");
      return `<span class="qa-group-badge is-multi-human" title="Multi-human Group Q&amp;A thread (${listStr})">👥 Group Q&amp;A · ${listStr}</span>`;
    }
    const soleAuthor = participants[0] || "";
    if ((c && c.is_shared) || (soleAuthor && viewerLdap && soleAuthor.toLowerCase() !== viewerLdap.toLowerCase())) {
      return `<span class="qa-group-badge" title="Shared team Q&amp;A thread${soleAuthor ? ` started by @${escapeHtml(soleAuthor)}` : ""}">👥 Shared${soleAuthor ? ` · @${escapeHtml(soleAuthor)}` : ""}</span>`;
    }
    return "";
  }

  function renderQuestionTurnItemHtml(num, c, itemId, rangeStr, turn, idx, isLastTurn, isPending) {
    const safeId = escapeHtml(itemId);
    const turnLabel = idx === 0 ? "Question" : `Follow-up #${idx}`;
    const hasTurnReply = Boolean(turn.reply && String(turn.reply).trim());
    const authorChipHtml = renderQuestionTurnAuthorChipHtml(turn, idx === 0 ? (c && (c.author || c.author_name)) : "");

    if (!isLastTurn || !isPending) {
      return `<div class="qa-turn-item">
        <div class="diff-inline-comment-body">${authorChipHtml}<strong>${turnLabel}:</strong> ${renderInlineMarkdownHtml(turn.question || "", false)}</div>
        ${hasTurnReply ? `<div class="diff-inline-agent-reply">
          <div class="diff-inline-agent-reply-head"><span>✨ Jetski Agent Reply</span></div>
          <div class="diff-inline-agent-reply-body">${renderAgentReplyMarkdownHtml(turn.reply)}</div>
        </div>` : ""}
      </div>`;
    }

    const isEditing = state.editingQaItemById[itemId] !== undefined;
    const actionBusy = state.qaActionInFlightById.has(itemId);
    const questionBlockHtml = isEditing
      ? `<div class="qa-inline-editor">
          <div style="font-size:11px;font-weight:700;color:var(--g-text);">✎ Modify in-flight ${turnLabel.toLowerCase()} (cancels current run &amp; resends):</div>
          <textarea id="qaModifyInput-${safeId}" class="qa-inline-textarea" oninput="state.editingQaItemById['${safeId}'] = this.value" onkeydown="onQaInlineTextareaKeydown(event, ${num}, '${safeId}', 'modify')">${escapeHtml(state.editingQaItemById[itemId])}</textarea>
          <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;">
            <button class="commit-msg-btn" ${actionBusy ? "disabled" : ""} onclick="cancelModifyInflightQuestion(${num}, '${safeId}')">Cancel</button>
            <button class="btn btn-swarm" style="padding:3px 10px;font-size:11px;" ${actionBusy ? "disabled" : ""} onclick="saveModifiedInflightQuestion(${num}, '${safeId}')">${actionBusy ? "Updating…" : "Save &amp; Resend"}</button>
          </div>
        </div>`
      : `<div class="qa-turn-question-row">
          <div class="diff-inline-comment-body" style="padding-bottom:2px;">${authorChipHtml}<strong>${turnLabel}:</strong> ${renderInlineMarkdownHtml(turn.question || "", false)}</div>
          <div class="qa-turn-inflight-btns">
            <button class="commit-msg-btn" ${actionBusy ? "disabled" : ""} onclick="startModifyInflightQuestion(${num}, '${safeId}')" title="Edit and resend this in-flight question">✎ Modify</button>
            <button class="commit-msg-btn qa-danger-btn" ${actionBusy ? "disabled" : ""} onclick="deleteInflightCodeQuestion(${num}, '${safeId}')" title="Cancel and delete this in-flight question">🗑 Delete</button>
          </div>
        </div>`;

    return `<div class="qa-turn-item">
      ${questionBlockHtml}
      <div class="diff-inline-agent-reply pending">
        <span class="btn-spinner"></span>
        <span>${actionBusy ? "Updating in-flight question…" : `Waiting for Jetski Agent reply on <code>${escapeHtml(c.alt_id ? rangeStr : `${c.file_path || ""}:${rangeStr}`)}</code>…`}</span>
      </div>
    </div>`;
  }

  function renderAgentQuestionCardHtml(num, c, itemId, rangeStr, collapsedCls, toggleLabel, singleLinePreview, filePath = "") {
    const safeId = escapeHtml(itemId);
    const turns = normalizeQuestionTurns(c);
    const latestTurn = turns[turns.length - 1];
    const isPending = isQuestionThreadPending(c);
    const hasReply = !isPending && Boolean(latestTurn && latestTurn.reply);
    const replyPreview = hasReply
      ? ` · ✨ ${String(latestTurn.reply).replace(/[#*`_>~]/g, "").replace(/\s+/g, " ").trim()}`
      : "";
    const statusText = hasReply
      ? (turns.length > 1 ? `✓ Replied (${turns.length} turns)` : "✓ Agent Replied")
      : (c.sending ? "Sending…" : "⏳ Agent Thinking…");
    const fnBadge = (c.function_name && c.function_name !== rangeStr)
      ? ` · <code>${escapeHtml(c.function_name)}</code>`
      : "";
    const groupBadgeHtml = renderQuestionGroupBadgeHtml(c, turns);
    const anchorTargetLabel = c.alt_id
      ? `AI Alternative · ${rangeStr}`
      : `${c.file_path || ""}:${rangeStr}`;
    const commentActionLabel = c.alt_id ? "✍️ Ask Author" : "💬 Comment on Block";
    const turnsHtml = turns.map((turn, idx) =>
      renderQuestionTurnItemHtml(num, c, itemId, rangeStr, turn, idx, idx === turns.length - 1, isPending)
    ).join("");

    const followupDraft = state.openFollowupByItemId[itemId];
    const followupEditorHtml = (!isPending && followupDraft !== undefined)
      ? `<div class="qa-inline-editor">
          <div style="font-size:11px;font-weight:700;color:var(--g-text);">🤖 Ask follow-up in this shared conversation:</div>
          <textarea id="qaFollowupInput-${safeId}" class="qa-inline-textarea" placeholder="Ask a follow-up question about ${escapeHtml(anchorTargetLabel)}…" oninput="state.openFollowupByItemId['${safeId}'] = this.value" onkeydown="onQaInlineTextareaKeydown(event, ${num}, '${safeId}', 'followup')">${escapeHtml(followupDraft)}</textarea>
          <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;">
            <button class="commit-msg-btn" onclick="cancelInlineFollowupComposer(${num}, '${safeId}')">Cancel</button>
            <button class="btn btn-swarm" style="padding:3px 10px;font-size:11px;" onclick="submitInlineFollowupQuestion(${num}, '${safeId}')">🤖 Send Follow-up</button>
          </div>
        </div>`
      : "";

    const lockedNoticeHtml = isPending
      ? `<div class="qa-locked-notice">🔒 Follow-up questions in this thread are blocked until the agent responds (use <strong>Modify</strong> or <strong>Delete</strong> above to change your pending question).</div>`
      : "";

    const effectiveFile = filePath || (c && (c.file_path || c.file)) || "";
    const threadNavHtml = (typeof renderPgCommentThreadNavHtml === "function")
      ? renderPgCommentThreadNavHtml(num, c, effectiveFile)
      : "";

    return `<div id="inlineItem-${safeId}" class="diff-inline-comment agent-question ${collapsedCls}" data-inline-id="${safeId}" data-cl="${num}" data-file="${escapeHtml(effectiveFile)}" data-line="${c.line || c.start_line || 1}" tabindex="0" role="region" aria-label="Asked Agent thread on ${escapeHtml(effectiveFile || "file")} ${escapeHtml(rangeStr)}" onkeydown="onPgCommentThreadKeyDown(event, ${num}, '${safeId}')">
      <div class="diff-inline-comment-head" onclick="toggleInlineItemCollapse(${num}, '${safeId}')" title="Click to expand or collapse agent conversation">
        <div class="diff-inline-head-left">
          <span class="diff-inline-head-title">🤖 Asked Agent · <code>${escapeHtml(rangeStr)}</code>${fnBadge}</span>
          ${groupBadgeHtml}
          <span class="diff-inline-preview">— "${escapeHtml(singleLinePreview)}"${escapeHtml(replyPreview)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
          ${threadNavHtml}
          <span class="diff-inline-status-pill ${hasReply ? "sent" : ""}">${escapeHtml(statusText)}</span>
          <button class="commit-msg-btn inline-collapse-btn" onclick="event.stopPropagation(); toggleInlineItemCollapse(${num}, '${safeId}')">${toggleLabel}</button>
        </div>
      </div>
      <div class="diff-inline-comment-body-wrap">
        ${turnsHtml}
        ${followupEditorHtml}
        ${lockedNoticeHtml}
        <div class="diff-inline-actions">
          <span style="font-size:11px;color:var(--g-muted);">Anchored to <code>${escapeHtml(anchorTargetLabel)}</code></span>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            ${!isPending && followupDraft === undefined ? `<button class="commit-msg-btn" onclick="openInlineFollowupComposer(${num}, '${safeId}')">🤖 Ask Follow-up</button>` : ""}
            <button class="commit-msg-btn" onclick="reopenInlineBlockInComposer(${num}, '${safeId}', 'comment')">${commentActionLabel}</button>
            <button class="commit-msg-btn" onclick="copyInlineAgentThread(${num}, '${safeId}')" title="Copy full multi-turn Q&amp;A thread as Markdown">📋 Copy Thread</button>
            <button class="commit-msg-btn" onclick="copyInlineAgentPrompt(${num}, '${safeId}')">Copy Prompt</button>
          </div>
        </div>
      </div>
    </div>`;
  }

  function renderInlineDraftCardHtml(num, c, itemId, rangeStr, collapsedCls, toggleLabel, singleLinePreview, filePath = "") {
    const safeId = escapeHtml(itemId);
    const effectiveFile = filePath || (c && (c.file_path || c.file)) || "";
    const threadNavHtml = (typeof renderPgCommentThreadNavHtml === "function")
      ? renderPgCommentThreadNavHtml(num, c, effectiveFile)
      : "";
    const modeLabel = c.draft_mode === "ask" ? "Ask Agent Draft" : "Comment Draft";
    const editState = state.openDraftEditByItemId && state.openDraftEditByItemId[itemId];
    const isEditing = Boolean(editState);
    const isCollapsed = !isEditing && Boolean(c.collapsed);
    const effectiveCollapsedCls = isCollapsed ? "collapsed" : "";
    const effectiveToggleLabel = isCollapsed ? "Show ▾" : "Hide ▴";
    const editingCls = isEditing ? "is-editing" : "";
    const isUnresolved = editState ? Boolean(editState.unresolved) : (c.unresolved !== false);
    const statusPillHtml = isUnresolved
      ? `<span id="inlineDraftPill-${safeId}" class="diff-inline-status-pill has-unresolved">⚠️ Unresolved</span>`
      : `<span id="inlineDraftPill-${safeId}" class="diff-inline-status-pill resolved">✓ Resolved</span>`;

    let bodyContentHtml = "";
    if (isEditing) {
      const activeTab = editState.tab || "edit";
      const draftVal = editState.draft !== undefined ? editState.draft : (c.message || "");
      const saveStatus = editState ? (editState.saveStatus || "saved") : "saved";
      const autosaveCls = `is-${saveStatus}`;
      const autosaveText = saveStatus === "unsaved" ? "● Unsaved" : (saveStatus === "saving" ? "Saving…" : "✓ Saved");
      const autosaveTitle = saveStatus === "unsaved"
        ? "Unsaved draft edits (autosaves after pause or on blur)"
        : (saveStatus === "saving" ? "Autosaving draft comment…" : "Draft changes automatically saved");
      const tabBodyHtml = activeTab === "preview"
        ? `<div class="diff-inline-comment-body pg-draft-preview-body">${renderInlineMarkdownHtml(draftVal || "*(Empty draft comment)*", false)}</div>`
        : `<textarea id="inlineDraftTextarea-${safeId}" class="qa-inline-textarea pg-inline-draft-textarea" placeholder="Write a draft comment (Markdown supported · Ctrl+Enter to save · Esc to cancel)…" oninput="onInlineDraftEditInput('${safeId}', this.value); autoResizePgTextarea(this);" onblur="flushPgInlineDraftAutosave(${num}, '${safeId}')" onkeydown="onInlineDraftEditKeydown(event, ${num}, '${safeId}')">${escapeHtml(draftVal)}</textarea>`;

      bodyContentHtml = `
        <div class="diff-inline-comment-body-wrap pg-draft-editor-wrap">
          <div class="pg-draft-tab-bar">
            <div class="pg-draft-tabs">
              <button type="button" class="pg-draft-tab ${activeTab !== "preview" ? "active" : ""}" onclick="event.stopPropagation(); setInlineDraftTab(${num}, '${safeId}', 'edit')">✎ Edit</button>
              <button type="button" class="pg-draft-tab ${activeTab === "preview" ? "active" : ""}" onclick="event.stopPropagation(); setInlineDraftTab(${num}, '${safeId}', 'preview')">👁 Preview</button>
            </div>
            <span class="pg-draft-loc-pill"><code>${escapeHtml(rangeStr)}</code></span>
          </div>
          ${tabBodyHtml}
          <div class="composer-footer pg-draft-editor-footer">
            <label class="composer-checkbox-label" for="inlineDraftResolvedChk-${safeId}">
              <input id="inlineDraftResolvedChk-${safeId}" type="checkbox" ${isUnresolved ? "checked" : ""} onchange="onInlineDraftResolvedChange('${safeId}', this.checked)" />
              <span>Unresolved</span>
              ${statusPillHtml}
            </label>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
              <span id="inlineDraftAutosaveBadge-${safeId}" class="pg-draft-autosave-badge ${autosaveCls}" title="${escapeHtml(autosaveTitle)}">${autosaveText}</span>
              <button type="button" class="commit-msg-btn" onclick="cancelInlineDraftInPlace(${num}, '${safeId}')">Cancel</button>
              <button type="button" class="commit-msg-btn" onclick="discardInlineDraftWithConfirm(${num}, '${safeId}', 'draft')">✕ Discard</button>
              <button id="btnSaveInlineDraft-${safeId}" type="button" class="btn btn-save-draft" onclick="saveInlineDraftInPlace(${num}, '${safeId}')">💾 Save</button>
              <button id="btnPostInlineDraft-${safeId}" type="button" class="btn btn-approve" style="padding:4px 10px;font-size:11.5px;" onclick="postInlineDraftToGerrit(${num}, '${safeId}')">💬 Post to Gerrit</button>
            </div>
          </div>
        </div>
      `;
    } else {
      bodyContentHtml = `
        <div class="diff-inline-comment-body-wrap">
          <div class="diff-inline-comment-body" ondblclick="editInlineDraftInPlace(${num}, '${safeId}')" title="Double-click or press 'e' to edit draft">${renderInlineMarkdownHtml(c.message || "", false)}</div>
          <div class="diff-inline-actions">
            <span style="font-size:11px;color:var(--g-muted);">Unsent draft comment (publish via Reply dialog)</span>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
              <button type="button" class="commit-msg-btn pg-btn-edit-draft" onclick="editInlineDraftInPlace(${num}, '${safeId}')" title="Edit draft in place (Shortcut: e)">✎ Edit</button>
              <button type="button" class="commit-msg-btn" onclick="discardInlineDraftWithConfirm(${num}, '${safeId}', 'draft')" title="Discard draft comment">✕ Discard</button>
              <button type="button" class="commit-msg-btn" onclick="reopenInlineBlockInComposer(${num}, '${safeId}', '${c.draft_mode === "ask" ? "ask" : "comment"}', true)" title="Reopen in code selection composer">✎ Composer</button>
              <button type="button" class="btn btn-approve" style="padding:3px 10px;font-size:11px;" onclick="postInlineDraftToGerrit(${num}, '${safeId}')" title="Post comment directly to Gerrit">💬 Post</button>
            </div>
          </div>
        </div>
      `;
    }

    return `<div id="inlineItem-${safeId}" class="diff-inline-comment draft-item ${editingCls} ${effectiveCollapsedCls}" data-inline-id="${safeId}" data-cl="${num}" data-file="${escapeHtml(effectiveFile)}" data-line="${c.line || c.start_line || 1}" tabindex="0" role="region" aria-label="Draft comment on ${escapeHtml(effectiveFile || "file")} ${escapeHtml(rangeStr)}" onkeydown="onPgCommentThreadKeyDown(event, ${num}, '${safeId}')">
      <div class="diff-inline-comment-head" onclick="toggleInlineItemCollapse(${num}, '${safeId}')" title="Click to expand or collapse saved draft">
        <div class="diff-inline-head-left">
          <span class="diff-inline-head-title">📝 ${escapeHtml(modeLabel)} · <code>${escapeHtml(rangeStr)}</code></span>
          <span class="diff-inline-preview">— "${escapeHtml(singleLinePreview)}"</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
          ${threadNavHtml}
          ${statusPillHtml}
          <span class="diff-inline-status-pill draft-pill">Draft</span>
          <button class="commit-msg-btn inline-collapse-btn" onclick="event.stopPropagation(); toggleInlineItemCollapse(${num}, '${safeId}')">${effectiveToggleLabel}</button>
        </div>
      </div>
      ${bodyContentHtml}
    </div>`;
  }

  function renderInlineCommentMessagesHtml(c) {
    const msgs = Array.isArray(c.messages) ? c.messages : [];
    if (msgs.length <= 1) {
      const singleMsg = c.message || (msgs[0] && msgs[0].message) || "";
      return `<div class="diff-inline-comment-body">${renderInlineMarkdownHtml(singleMsg, false)}</div>`;
    }
    return msgs.map(m => {
      const isMsgRobot = Boolean(m.is_robot || m.robot_id || (m.author_name && isPgRobotComment({ author_name: m.author_name })));
      const robotBadge = (isMsgRobot && (m.robot_id || m.author_name))
        ? `<span class="pg-robot-badge">🤖 ${escapeHtml(m.robot_id || m.author_name)}</span>`
        : "";
      const runPill = (isMsgRobot && m.robot_run_id)
        ? `<span class="pg-robot-run-pill" title="Robot Run ID">Run #${escapeHtml(m.robot_run_id)}</span>`
        : "";
      const runLink = (isMsgRobot && m.url)
        ? `<a href="${escapeHtml(m.url)}" target="_blank" rel="noopener noreferrer" class="pg-robot-link" onclick="event.stopPropagation()">View Run ↗</a>`
        : "";
      const author = escapeHtml(m.author_name || c.author_name || (isMsgRobot ? "Robot" : "reviewer"));
      const psBadge = m.patch_set
        ? `<span class="diff-inline-status-pill">PS${escapeHtml(m.patch_set)}</span>`
        : "";
      const msgFixHtml = (Array.isArray(m.fix_suggestions) && m.fix_suggestions.length > 0)
        ? renderPgFixSuggestionsHtml(m.fix_suggestions, c.id || "msg", Number(m.patch_set || c.patch_set || 1))
        : "";
      return `<div class="inline-thread-msg ${isMsgRobot ? "is-robot-msg" : ""}">
        <div class="inline-thread-msg-meta">
          <span>${isMsgRobot ? "🤖" : "💬"} ${author}</span>
          ${robotBadge}
          ${runPill}
          ${runLink}
          ${psBadge}
        </div>
        <div class="diff-inline-comment-body">${renderInlineMarkdownHtml(m.message || "", false)}</div>
        ${msgFixHtml}
      </div>`;
    }).join("");
  }

  function formatInlineReplyStatusPillText(wasResolved, nextResolved) {
    if (nextResolved) {
      return wasResolved ? "Stays Resolved" : "Marks Resolved";
    }
    return wasResolved ? "Reopens Unresolved" : "Keeps Unresolved";
  }

  function renderInlineReplyEditorHtml(num, c, safeId, rangeStr) {
    const replyState = state.openReplyByItemId[c.id || safeId];
    if (!replyState) return "";
    const wasResolved = c.unresolved === false;
    const isResolved = !replyState.unresolved;
    const statusPillText = formatInlineReplyStatusPillText(wasResolved, isResolved);
    const submitLabel = replyState.submitting
      ? `<span class="btn-spinner"></span> Sending Reply…`
      : (isResolved && !wasResolved ? "✓ Send Reply &amp; Resolve" : "💬 Send Reply");
    const errHtml = replyState.error
      ? `<div class="inline-reply-error">${escapeHtml(replyState.error)}</div>`
      : "";
    const targetAuthor = escapeHtml(c.root_author_name || c.author_name || "thread");
    const targetLoc = `${escapeHtml(c.file_path || "")} ${escapeHtml(rangeStr)}`.trim();
    const replySaveStatus = replyState.saveStatus || (replyState.isSavedDraft ? "saved" : "saved");
    const replyAutosaveCls = `is-${replySaveStatus}`;
    const replyAutosaveText = replySaveStatus === "unsaved" ? "● Unsaved" : (replySaveStatus === "saving" ? "Saving…" : "✓ Saved");
    const replyAutosaveTitle = replySaveStatus === "unsaved"
      ? "Unsaved reply draft edits (autosaves after pause or on blur)"
      : (replySaveStatus === "saving" ? "Autosaving reply draft…" : "Reply draft changes automatically saved");

    return `<div class="inline-reply-editor" onclick="event.stopPropagation()">
      <label class="inline-reply-label" for="commentReplyInput-${safeId}">Reply to ${targetAuthor} on <code>${escapeHtml(rangeStr)}</code> (notify: OWNER):</label>
      <textarea
        id="commentReplyInput-${safeId}"
        class="qa-inline-textarea"
        aria-label="Reply to inline comment on ${targetLoc}"
        placeholder="Write a reply (Markdown supported · Ctrl+Enter to send · Esc to cancel)…"
        ${replyState.submitting ? "disabled" : ""}
        oninput="onInlineReplyDraftInput('${safeId}', this.value)"
        onblur="flushPgReplyDraftAutosave(${num}, '${safeId}')"
        onkeydown="onInlineReplyKeydown(event, ${num}, '${safeId}')"
      >${escapeHtml(replyState.draft || "")}</textarea>
      ${errHtml}
      <div class="composer-footer">
        <label class="composer-checkbox-label" for="commentReplyResolved-${safeId}">
          <input
            id="commentReplyResolved-${safeId}"
            type="checkbox"
            ${isResolved ? "checked" : ""}
            ${replyState.submitting ? "disabled" : ""}
            onchange="onInlineReplyResolvedChange(${num}, '${safeId}', this.checked)"
          />
          <span>Resolved</span>
          <span id="commentReplyPill-${safeId}" class="diff-inline-status-pill ${isResolved ? "sent" : ""}">${statusPillText}</span>
        </label>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
          <span id="inlineReplyAutosaveBadge-${safeId}" class="pg-draft-autosave-badge ${replyAutosaveCls}" title="${escapeHtml(replyAutosaveTitle)}">${replyAutosaveText}</span>
          <button type="button" class="commit-msg-btn quick-pill quote-pill" onclick="quoteInlineCommentInReply('${safeId}', ${num})" title="Quote parent comment into reply text (Shortcut: q)">Quote (q)</button>
          <button type="button" class="commit-msg-btn quick-pill" onclick="setInlineReplyQuickText('${safeId}', ${num}, 'Done')">Done</button>
          <button type="button" class="commit-msg-btn quick-pill" onclick="setInlineReplyQuickText('${safeId}', ${num}, 'Ack')">Ack</button>
          <button type="button" class="commit-msg-btn" ${replyState.submitting ? "disabled" : ""} onclick="saveInlineReplyAsDraft(${num}, '${safeId}')" title="Save draft to publish later with Reply dialog">Save Draft</button>
          <button type="button" class="commit-msg-btn" ${replyState.submitting ? "disabled" : ""} onclick="cancelInlineReplyComposer(${num}, '${safeId}')">Cancel</button>
          <button id="btnSubmitInlineReply-${safeId}" type="button" class="btn btn-approve" style="padding:4px 10px;font-size:11.5px;" ${replyState.submitting ? "disabled" : ""} onclick="submitInlineCommentReply(${num}, '${safeId}')">${submitLabel}</button>
        </div>
      </div>
    </div>`;
  }

  function getPgCommentThreadNavInfo(clNum, c, filePath = "") {
    const num = Number(clNum || state.selectedClNumber || 0);
    const effFile = filePath || (c && (c.file_path || c.file || c.path)) || "";
    if (!effFile) {
      return { index: 1, total: 1, filePath: "" };
    }
    const allComments = (typeof getInlineCommentsForFile === "function") ? getInlineCommentsForFile(num, effFile) : [];
    if (!allComments.length) {
      return { index: 1, total: 1, filePath: effFile };
    }
    const targetId = c && c.id;
    const foundIdx = allComments.findIndex(item => {
      if (!item) return false;
      if (targetId && item.id && String(item.id) === String(targetId)) return true;
      if (item.line != null && c && c.line != null && Number(item.line) === Number(c.line)) {
        if (item.message && c.message && item.message === c.message) return true;
      }
      return false;
    });
    const index = foundIdx !== -1 ? foundIdx + 1 : 1;
    return {
      index,
      total: allComments.length,
      filePath: effFile,
    };
  }

  function renderPgCommentThreadNavHtml(clNum, c, filePath = "") {
    const num = Number(clNum || state.selectedClNumber || 0);
    const nav = getPgCommentThreadNavInfo(num, c, filePath);
    const safeFile = escapeHtml(nav.filePath).replace(/'/g, "&#39;");
    const safeId = escapeHtml((c && c.id) || "");
    const baseName = nav.filePath ? (nav.filePath.split("/").pop() || nav.filePath) : "file";
    return `<div class="pg-thread-nav" onclick="event.stopPropagation()">
      <span class="pg-thread-pos-pill" title="Comment thread ${nav.index} of ${nav.total} in ${escapeHtml(baseName)} (Alt+N / Alt+P or j/k)">${nav.index}/${nav.total}</span>
      <button type="button" class="commit-msg-btn pg-btn-thread-nav btn-thread-prev" onclick="event.stopPropagation(); navigatePgFileCommentThread('prev', ${num}, '${safeFile}', '${safeId}')" title="Previous comment thread in ${escapeHtml(baseName)} (Alt+P or k)"><span aria-hidden="true">▲</span></button>
      <button type="button" class="commit-msg-btn pg-btn-thread-nav btn-thread-next" onclick="event.stopPropagation(); navigatePgFileCommentThread('next', ${num}, '${safeFile}', '${safeId}')" title="Next comment thread in ${escapeHtml(baseName)} (Alt+N or j)"><span aria-hidden="true">▼</span></button>
      <button type="button" class="commit-msg-btn pg-btn-thread-nav btn-thread-file-top" onclick="event.stopPropagation(); scrollToPgFileHeader(${num}, '${safeFile}')" title="Jump to file diff header for ${escapeHtml(baseName)} (u)"><span aria-hidden="true">↑</span> File</button>
    </div>`;
  }

  function navigatePgFileCommentThread(dir, clNum, filePath, curItemId) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const safeFile = filePath || "";
    const baseName = safeFile ? (safeFile.split("/").pop() || safeFile) : "file";

    let targetEls = [];
    if (safeFile === "/COMMIT_MSG") {
      const msgBox = document.getElementById(`commitMsgPanel-${num}`) || document.querySelector(`.commit-msg-table[data-cl="${num}"]`);
      if (msgBox) {
        targetEls = Array.from(msgBox.querySelectorAll(".diff-inline-comment"));
      }
    } else if (safeFile) {
      const escapedPath = safeFile.replace(/["\\]/g, "\\$&");
      const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${escapedPath}"]`) ||
                        document.querySelector(`.pg-file-diff-block[data-file="${escapedPath}"]`);
      if (fileBlock) {
        targetEls = Array.from(fileBlock.querySelectorAll(".diff-inline-comment"));
      }
    }
    if (targetEls.length === 0) {
      const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
      if (changeView) {
        targetEls = Array.from(changeView.querySelectorAll(".diff-inline-comment"));
      }
    }

    if (targetEls.length === 0) {
      if (typeof showToast === "function") showToast("No comment threads found in file");
      return;
    }

    let curIdx = targetEls.findIndex(el => el.getAttribute("data-inline-id") === curItemId || el.id === `inlineItem-${curItemId}`);
    if (curIdx === -1) curIdx = 0;

    let targetIdx = curIdx;
    if (dir === "next") {
      targetIdx = (curIdx + 1) % targetEls.length;
    } else if (dir === "prev") {
      targetIdx = (curIdx - 1 + targetEls.length) % targetEls.length;
    }

    const targetEl = targetEls[targetIdx];
    if (!targetEl) return;

    if (safeFile === "/COMMIT_MSG") {
      if (typeof uncollapseCommitMessage === "function") uncollapseCommitMessage(num, true);
    } else if (safeFile) {
      const escapedPath = safeFile.replace(/["\\]/g, "\\$&");
      const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${escapedPath}"]`) ||
                        document.querySelector(`.pg-file-diff-block[data-file="${escapedPath}"]`);
      if (fileBlock && fileBlock.classList.contains("is-collapsed")) {
        const curPs = Number(fileBlock.getAttribute("data-ps") || 1);
        if (typeof setPgFileCollapsed === "function") setPgFileCollapsed(num, curPs, safeFile, false);
        fileBlock.classList.remove("is-collapsed");
        const codeTbl = fileBlock.querySelector(".code-table");
        if (codeTbl && codeTbl.style) codeTbl.style.display = "";
      }
    }

    if (targetEl.classList.contains("collapsed")) {
      targetEl.classList.remove("collapsed");
      const btn = targetEl.querySelector(".inline-collapse-btn");
      if (btn) btn.textContent = "Hide ▴";
      const idAttr = targetEl.getAttribute("data-inline-id");
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

    if (typeof targetEl.scrollIntoView === "function") {
      targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    targetEl.classList.remove("pg-unresolved-focused");
    void targetEl.offsetWidth;
    targetEl.classList.add("pg-unresolved-focused");
    setTimeout(() => targetEl.classList.remove("pg-unresolved-focused"), 2200);

    if (typeof targetEl.focus === "function") {
      try { targetEl.focus({ preventScroll: true }); } catch (err) {}
    }

    state.activeCommentIndexByFile = state.activeCommentIndexByFile || {};
    state.activeCommentIndexByFile[`${num}:${safeFile}:all`] = targetIdx;
    if (typeof focusPgFileRow === "function" && safeFile) {
      focusPgFileRow(num, safeFile, { scrollIntoView: false });
    }
    if (typeof showToast === "function") {
      showToast(`Thread ${targetIdx + 1} of ${targetEls.length} in ${baseName}`);
    }
  }

  function scrollToPgFileHeader(clNum, filePath) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num || !filePath) return;
    if (typeof selectPgChangeTab === "function") {
      selectPgChangeTab(num, "files");
    }
    const safeFile = filePath;
    const baseName = safeFile.split("/").pop() || safeFile;

    if (safeFile === "/COMMIT_MSG" || safeFile === "COMMIT_MSG") {
      const msgBox = document.getElementById(`commitMsgPanel-${num}`) || document.getElementById(`commitMessagePanel-${num}`) || document.querySelector(`.commit-msg-table[data-cl="${num}"]`);
      if (msgBox) {
        if (typeof uncollapseCommitMessage === "function") uncollapseCommitMessage(num, true);
        if (typeof msgBox.scrollIntoView === "function") {
          msgBox.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        msgBox.classList.add("pg-header-focused-flash");
        setTimeout(() => msgBox.classList.remove("pg-header-focused-flash"), 1800);
      }
      if (typeof showToast === "function") showToast("Jumped to Commit Message header");
      return;
    }

    if (typeof isPgFileFilterActive === "function" && isPgFileFilterActive(num)) {
      if (typeof clearAllPgFileFilters === "function") {
        clearAllPgFileFilters(num, false);
      }
    }

    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    const container = changeView || document;
    let fileBlock = Array.from(container.querySelectorAll(`.pg-file-diff-block[data-cl="${num}"]`))
      .find(b => b.getAttribute("data-file") === safeFile);
    if (!fileBlock) {
      fileBlock = Array.from(container.querySelectorAll(".pg-file-diff-block"))
        .find(b => b.getAttribute("data-file") === safeFile);
    }
    if (fileBlock) {
      const ps = Number(fileBlock.getAttribute("data-ps") || 1);
      if (fileBlock.classList.contains("is-collapsed") && typeof setPgFileCollapsed === "function") {
        setPgFileCollapsed(num, ps, safeFile, false);
      }
      const header = fileBlock.querySelector(".file-header") || fileBlock;
      if (typeof header.scrollIntoView === "function") {
        header.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      header.classList.add("pg-header-focused-flash");
      setTimeout(() => header.classList.remove("pg-header-focused-flash"), 1800);
      if (typeof showToast === "function") showToast(`Jumped to header for ${baseName}`);
    } else {
      if (typeof scrollToPgFileList === "function") {
        scrollToPgFileList(num);
      }
    }
  }

  function navigatePgCommentThread(dir = "next", clNum = null, opts = {}) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;

    const cards = Array.from(changeView.querySelectorAll(
      ".diff-inline-comment:not([style*='display: none']):not(.pg-author-hidden)"
    )).filter(el => {
      const block = el.closest(".pg-file-diff-block");
      if (block && block.style && block.style.display === "none") return false;
      return true;
    });

    if (cards.length === 0) {
      if (typeof showToast === "function") showToast("No comment threads on this change");
      return;
    }

    const activeEl = (typeof document !== "undefined" && document.activeElement) ? document.activeElement : null;
    const focusedCard = activeEl ? (activeEl.closest ? activeEl.closest(".diff-inline-comment") : null) : null;
    let curIdx = focusedCard ? cards.indexOf(focusedCard) : -1;

    if (curIdx === -1) {
      const container = document.getElementById("diffPane") || document.getElementById("diffScroll") || window;
      const viewTop = (container && container.scrollTop !== undefined) ? container.scrollTop : 0;
      const offsetThreshold = 50;
      for (let i = 0; i < cards.length; i++) {
        const top = cards[i].offsetTop || 0;
        if (top >= viewTop - offsetThreshold) {
          curIdx = i;
          break;
        }
      }
    }

    if (curIdx === -1) {
      curIdx = (dir === "prev" ? cards.length - 1 : 0);
    }

    let targetIdx = curIdx;
    if (dir === "next") {
      targetIdx = (curIdx + 1) % cards.length;
    } else if (dir === "prev") {
      targetIdx = (curIdx - 1 + cards.length) % cards.length;
    }

    const targetEl = cards[targetIdx];
    if (!targetEl) return;

    const parentBlock = targetEl.closest(".pg-file-diff-block");
    if (parentBlock && parentBlock.classList.contains("is-collapsed")) {
      const parentFile = parentBlock.getAttribute("data-file") || "";
      const curPs = Number(parentBlock.getAttribute("data-ps") || 1);
      if (typeof setPgFileCollapsed === "function") setPgFileCollapsed(num, curPs, parentFile, false);
      parentBlock.classList.remove("is-collapsed");
      const codeTbl = parentBlock.querySelector(".code-table");
      if (codeTbl && codeTbl.style) codeTbl.style.display = "";
    }

    const commitMsgPanel = targetEl.closest(".commit-msg-table") || targetEl.closest(".commit-msg-box") || targetEl.closest(`[id^="commitMsgPanel-"]`);
    if (commitMsgPanel && typeof uncollapseCommitMessage === "function") {
      uncollapseCommitMessage(num, true);
    }

    if (targetEl.classList.contains("collapsed")) {
      const idAttr = targetEl.getAttribute("data-inline-id");
      if (idAttr) {
        toggleInlineItemCollapse(num, idAttr);
      } else {
        targetEl.classList.remove("collapsed");
      }
    }

    if (typeof targetEl.scrollIntoView === "function") {
      targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    targetEl.classList.remove("pg-unresolved-focused");
    void targetEl.offsetWidth;
    targetEl.classList.add("pg-unresolved-focused");
    setTimeout(() => {
      targetEl.classList.remove("pg-unresolved-focused");
    }, 2200);

    if (typeof targetEl.focus === "function") {
      try { targetEl.focus({ preventScroll: true }); } catch (err) {}
    }

    const safeFile = targetEl.getAttribute("data-file") || "";
    const baseName = safeFile ? (safeFile.split("/").pop() || safeFile) : "file";
    const isUnres = targetEl.getAttribute("data-unresolved") !== "false";
    const isDraft = targetEl.classList.contains("draft-item");
    const isAgent = targetEl.classList.contains("agent-question");
    const kindLabel = isDraft ? " · Draft" : (isAgent ? " · Agent" : (isUnres ? " · Unresolved" : " · Resolved"));

    if (typeof showToast === "function") {
      showToast(`Comment thread ${targetIdx + 1} of ${cards.length} in ${baseName}${kindLabel} (Alt+N / Alt+P)`);
    }
  }

  function onPgCommentThreadKeyDown(event, clNum, itemId) {
    if (!event) return;
    const target = event.target;
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
      return;
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const idStr = String(itemId || "");
    const key = event.key;

    if (key === "e" || key === "E") {
      event.preventDefault();
      event.stopPropagation();
      const cardEl = document.getElementById(`inlineItem-${idStr}`);
      if (cardEl && cardEl.classList.contains("draft-item") && !cardEl.classList.contains("is-editing")) {
        if (typeof editInlineDraftInPlace === "function") editInlineDraftInPlace(num, idStr);
      } else {
        toggleInlineItemCollapse(num, idStr);
        const isNowCollapsed = cardEl && cardEl.classList.contains("collapsed");
        if (typeof showToast === "function") {
          showToast(isNowCollapsed ? "Collapsed comment thread (e)" : "Expanded comment thread (e)");
        }
      }
      return;
    }

    if (key === "r" || key === "R") {
      event.preventDefault();
      event.stopPropagation();
      if (typeof openInlineReplyComposer === "function") openInlineReplyComposer(num, idStr);
      return;
    }

    if (key === "d" || key === "D") {
      const cardEl = document.getElementById(`inlineItem-${idStr}`);
      const isUnresolved = cardEl && cardEl.getAttribute("data-unresolved") !== "false";
      if (isUnresolved && typeof quickReplyInlineComment === "function") {
        event.preventDefault();
        event.stopPropagation();
        quickReplyInlineComment(num, idStr, "Done");
        return;
      }
    }

    if (key === "a" || key === "A") {
      const cardEl = document.getElementById(`inlineItem-${idStr}`);
      const isUnresolved = cardEl && cardEl.getAttribute("data-unresolved") !== "false";
      if (isUnresolved && typeof quickReplyInlineComment === "function") {
        event.preventDefault();
        event.stopPropagation();
        quickReplyInlineComment(num, idStr, "Ack");
        return;
      }
    }

    if (key === "j" || key === "ArrowDown" || key === "n") {
      event.preventDefault();
      event.stopPropagation();
      navigatePgCommentThread("next", num);
      return;
    }

    if (key === "k" || key === "ArrowUp" || key === "p") {
      event.preventDefault();
      event.stopPropagation();
      navigatePgCommentThread("prev", num);
      return;
    }

    if (key === "u" || key === "U") {
      event.preventDefault();
      event.stopPropagation();
      const cardEl = document.getElementById(`inlineItem-${idStr}`);
      const file = cardEl ? cardEl.getAttribute("data-file") : "";
      if (file && typeof scrollToPgFileHeader === "function") scrollToPgFileHeader(num, file);
      return;
    }

    if (key === "Enter" || key === " ") {
      const cardEl = document.getElementById(`inlineItem-${idStr}`);
      if (cardEl && cardEl.classList.contains("collapsed")) {
        event.preventDefault();
        event.stopPropagation();
        toggleInlineItemCollapse(num, idStr);
        if (typeof showToast === "function") showToast("Expanded comment thread");
        return;
      }
    }

    if (key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (target && typeof target.blur === "function") target.blur();
      return;
    }
  }

  function renderInlineCommentCardHtml(clNum, c, filePath = null) {
    const num = Number(clNum);
    const itemId = c.id || `inline-${state.nextInlineItemId++}`;
    const safeId = escapeHtml(itemId);
    const kind = c.kind || "comment";
    const rangeStr = c.alt_id
      ? (c.alt_title || c.alt_id)
      : ((c.start_line && c.end_line && Number(c.end_line) > Number(c.start_line))
        ? `L${c.start_line}–L${c.end_line}`
        : `L${c.line || 1}`);
    const isCollapsed = Boolean(c.collapsed);
    const collapsedCls = isCollapsed ? "collapsed" : "";
    const toggleLabel = isCollapsed ? "Show ▾" : "Hide ▴";
    const singleLinePreview = String(c.message || "").replace(/\s+/g, " ").trim();
    const effectiveFile = filePath || (c && (c.file_path || c.file || c.path)) || "";

    if (kind === "agent_question") {
      return renderAgentQuestionCardHtml(num, c, itemId, rangeStr, collapsedCls, toggleLabel, singleLinePreview, effectiveFile);
    }
    if (kind === "draft") {
      return renderInlineDraftCardHtml(num, c, itemId, rangeStr, collapsedCls, toggleLabel, singleLinePreview, effectiveFile);
    }

    const threadNavHtml = (typeof renderPgCommentThreadNavHtml === "function")
      ? renderPgCommentThreadNavHtml(num, c, effectiveFile)
      : "";

    const statusLabel = c.unresolved === false ? "Resolved" : "Unresolved";
    const resolvedCls = c.unresolved === false ? "resolved" : "";
    const msgCount = Array.isArray(c.messages) ? c.messages.length : 0;
    const countSuffix = msgCount > 1 ? ` · ${msgCount} msgs` : "";
    const displayAuthor = c.root_author_name || c.author_name || "you";
    const isUnresolved = c.unresolved !== false;
    const isRobot = isPgRobotComment(c);
    const robotCls = isRobot ? "is-robot" : "";
    const robotAttr = isRobot ? 'data-is-robot="1"' : "";
    const robotId = c.robot_id || (c.messages && c.messages.find(m => m && m.robot_id)?.robot_id) || (isRobot ? (c.author_name || "Robot") : "");
    const robotRunId = c.robot_run_id || (c.messages && c.messages.find(m => m && m.robot_run_id)?.robot_run_id) || "";
    const robotUrl = c.url || (c.messages && c.messages.find(m => m && m.url)?.url) || "";
    const fixSuggestions = (Array.isArray(c.fix_suggestions) && c.fix_suggestions.length > 0)
      ? c.fix_suggestions
      : ((c.messages && c.messages.find(m => Array.isArray(m.fix_suggestions) && m.fix_suggestions.length > 0)?.fix_suggestions) || []);
    const fixSuggestionsHtml = renderPgFixSuggestionsHtml(fixSuggestions, safeId, num);

    const robotBadgeHtml = (isRobot && (robotId || c.author_name))
      ? `<span class="pg-robot-badge">🤖 ${escapeHtml(robotId || c.author_name)}</span>`
      : "";
    const robotRunHtml = (isRobot && robotRunId)
      ? `<span class="pg-robot-run-pill" title="Robot Run ID">Run #${escapeHtml(robotRunId)}</span>`
      : "";
    const robotLinkHtml = (isRobot && robotUrl)
      ? `<a href="${escapeHtml(robotUrl)}" target="_blank" rel="noopener noreferrer" class="pg-robot-link" onclick="event.stopPropagation()">View Run ↗</a>`
      : "";
    const quickActionsHtml = isUnresolved ? `
      <button type="button" class="commit-msg-btn inline-quick-btn inline-done-btn" onclick="event.stopPropagation(); quickReplyInlineComment(${num}, '${safeId}', 'Done')" title="Reply 'Done' and mark thread resolved">✓ Done</button>
      <button type="button" class="commit-msg-btn inline-quick-btn inline-ack-btn" onclick="event.stopPropagation(); quickReplyInlineComment(${num}, '${safeId}', 'Ack')" title="Reply 'Ack' and mark thread resolved">✓ Ack</button>
    ` : "";
    const savedReplyDraft = (state.openReplyByItemId[safeId] && state.openReplyByItemId[safeId].isSavedDraft && state.openReplyByItemId[safeId].draft) ? state.openReplyByItemId[safeId] : null;
    const savedDraftBanner = savedReplyDraft ? `
      <div class="diff-inline-saved-reply-draft">
        <span>📝 <strong>Unsent Reply Draft:</strong> "${escapeHtml(savedReplyDraft.draft)}" (${savedReplyDraft.unresolved ? "Unresolved" : "Marks Resolved"})</span>
        <div style="display:flex;align-items:center;gap:6px;">
          <a href="javascript:void(0)" class="pg-link-btn" onclick="event.stopPropagation(); openInlineReplyComposer(${num}, '${safeId}')">✎ Edit</a>
          <a href="javascript:void(0)" class="pg-link-btn" style="color:var(--g-error, #f28b82);" onclick="event.stopPropagation(); discardReplyDialogDraftWithConfirm(${num}, '${safeId}', 'reply')">✕ Discard</a>
        </div>
      </div>
    ` : "";

    const rootIdAttr = c.root_id ? `data-root-id="${escapeHtml(c.root_id)}"` : "";
    const leafIdAttr = c.leaf_id ? `data-leaf-id="${escapeHtml(c.leaf_id)}"` : "";
    const commentIdAttr = c.comment_id ? `data-comment-id="${escapeHtml(c.comment_id)}"` : "";
    const serverIdAttr = c.server_id ? `data-server-id="${escapeHtml(c.server_id)}"` : "";

    const curAuthorFilter = (typeof getPgCommentsAuthorFilter === "function") ? getPgCommentsAuthorFilter(num) : "all";
    const isAuthorHidden = Boolean(curAuthorFilter && curAuthorFilter !== "all" && curAuthorFilter !== displayAuthor);
    const authorHiddenCls = isAuthorHidden ? "pg-author-hidden" : "";

    return `<div id="inlineItem-${safeId}" class="diff-inline-comment ${robotCls} ${resolvedCls} ${collapsedCls} ${authorHiddenCls}" data-inline-id="${safeId}" ${rootIdAttr} ${leafIdAttr} ${commentIdAttr} ${serverIdAttr} data-cl="${num}" data-file="${escapeHtml(effectiveFile)}" data-line="${c.line || c.start_line || 1}" data-unresolved="${isUnresolved ? "true" : "false"}" data-author="${escapeHtml(displayAuthor)}" data-author-email="${escapeHtml(c.author_email || "")}" ${robotAttr} ${isRobot ? `data-robot-id="${escapeHtml(robotId)}"` : ""} tabindex="0" role="region" aria-label="Comment thread on ${escapeHtml(effectiveFile || "file")} ${escapeHtml(rangeStr)} by ${escapeHtml(displayAuthor)} (${statusLabel})" onkeydown="onPgCommentThreadKeyDown(event, ${num}, '${safeId}')">
      <div class="diff-inline-comment-head" onclick="toggleInlineItemCollapse(${num}, '${safeId}')" title="Click to expand or collapse inline comment">
        <div class="diff-inline-head-left">
          <span class="diff-inline-head-title">${isRobot ? "🤖" : "💬"} <span class="pg-inline-author-chip" onclick="event.stopPropagation(); togglePgDiffAuthorFilter(${num}, '${escapeJsQuote(displayAuthor)}')" title="Filter diff comments to ${escapeHtml(displayAuthor)}">${escapeHtml(displayAuthor)}</span> · <code>${escapeHtml(rangeStr)}</code> (${statusLabel}${countSuffix}) ${robotBadgeHtml} ${robotRunHtml} ${robotLinkHtml}</span>
          <span class="diff-inline-preview">— "${escapeHtml(singleLinePreview)}"</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
          ${threadNavHtml}
          <button type="button" class="commit-msg-btn inline-reply-btn" onclick="event.stopPropagation(); openInlineReplyComposer(${num}, '${safeId}')" title="Reply to this inline comment thread">💬 Reply</button>
          ${quickActionsHtml}
          <button type="button" class="nudge-btn" onclick="event.stopPropagation(); triggerHarnessForCl(${num}, 'address-review')">→ Auto-Fix with Agent</button>
          <button type="button" class="commit-msg-btn inline-collapse-btn" onclick="event.stopPropagation(); toggleInlineItemCollapse(${num}, '${safeId}')">${toggleLabel}</button>
        </div>
      </div>
      <div class="diff-inline-comment-body-wrap">
        ${renderInlineCommentMessagesHtml(c)}
        ${fixSuggestionsHtml}
        ${savedDraftBanner}
        ${renderInlineReplyEditorHtml(num, c, safeId, rangeStr)}
      </div>
    </div>`;
  }

  function renderInlineLineCommentsHtml(clNum, filePath, oldLn, newLn, fileComments, startLn = null, lineIdx = null) {
    if (!Array.isArray(fileComments) || fileComments.length === 0) return "";
    const matched = fileComments.filter(c => {
      if (c.end_idx != null && lineIdx != null) {
        return Number(c.end_idx) === Number(lineIdx);
      }
      if (filePath === "/COMMIT_MSG" && startLn != null && newLn !== "") {
        const cLn = Number(c.line);
        return cLn >= Number(startLn) && cLn <= Number(newLn);
      }
      const targetSide = String(c.side || "REVISION").toUpperCase();
      if (targetSide === "PARENT") {
        return oldLn !== "" && Number(c.line) === Number(oldLn);
      }
      return newLn !== "" && Number(c.line) === Number(newLn);
    });
    if (matched.length === 0) return "";
    return matched.map(c => renderInlineCommentCardHtml(clNum, c, filePath)).join("");
  }

  function buildPgFileCommentsLineIndex(fileComments) {
    if (!Array.isArray(fileComments) || fileComments.length === 0) return null;
    const leftLines = new Set();
    const rightLines = new Set();
    let hasIdxRange = false;
    for (let i = 0; i < fileComments.length; i++) {
      const c = fileComments[i];
      if (!c) continue;
      if (c.start_idx != null || c.end_idx != null) {
        hasIdxRange = true;
      }
      const isBase = String(c.side || "REVISION").toUpperCase() === "PARENT";
      const targetSet = isBase ? leftLines : rightLines;
      const sLn = Number(c.start_line || c.line || 0);
      const eLn = Number(c.end_line || c.line || 0);
      if (sLn > 0 && eLn >= sLn && eLn - sLn <= 500) {
        for (let ln = sLn; ln <= eLn; ln++) {
          targetSet.add(ln);
        }
      } else if (sLn > 0) {
        targetSet.add(sLn);
        if (eLn > sLn) hasIdxRange = true;
      } else {
        hasIdxRange = true;
      }
    }
    return { leftLines, rightLines, hasIdxRange };
  }

  function computePgFileCommentsCacheSig(clNum, fileComments) {
    if (!Array.isArray(fileComments) || fileComments.length === 0) return "0";
    const curAuthorFilter = (typeof getPgCommentsAuthorFilter === "function") ? getPgCommentsAuthorFilter(clNum) : "all";
    if (fileComments.length === 1) {
      const c0 = fileComments[0];
      if (c0) {
        const id0 = c0.id || c0.comment_id || "";
        const reply0 = state.openReplyByItemId && state.openReplyByItemId[id0];
        const edit0 = state.openDraftEditByItemId && state.openDraftEditByItemId[id0];
        if (!reply0 && !edit0) {
          const msgsLen0 = Array.isArray(c0.messages) ? c0.messages.length : 0;
          const csSub = fileComments._csigSub;
          if (
            csSub &&
            csSub.cRef === c0 &&
            csSub.authorFilter === curAuthorFilter &&
            csSub.id === id0 &&
            csSub.kind === (c0.kind || "") &&
            csSub.line === (c0.line || 0) &&
            csSub.startLine === (c0.start_line || 0) &&
            csSub.endLine === (c0.end_line || 0) &&
            csSub.startIdx === (c0.start_idx ?? "") &&
            csSub.endIdx === (c0.end_idx ?? "") &&
            csSub.side === (c0.side || "") &&
            csSub.unres === (c0.unresolved !== false) &&
            csSub.collapsed === Boolean(c0.collapsed) &&
            csSub.msg === (c0.message || "") &&
            csSub.msgsLen === msgsLen0 &&
            csSub.draftMode === (c0.draft_mode || "") &&
            csSub.status === (c0.status || "") &&
            csSub.reply === (c0.reply || "") &&
            csSub.err === (c0.error || "")
          ) {
            return csSub.sig;
          }
          const sig0 = `1:${curAuthorFilter};${id0}:${c0.kind || ""}:${c0.line || 0}:${c0.start_line || 0}:${c0.end_line || 0}:${c0.start_idx ?? ""}:${c0.end_idx ?? ""}:${c0.side || ""}:${c0.unresolved === false ? 0 : 1}:${c0.collapsed ? 1 : 0}:${c0.message || ""}:${msgsLen0}:${c0.draft_mode || ""}:${c0.status || ""}:${c0.reply || ""}:${c0.error || ""}::`;
          fileComments._csigSub = {
            cRef: c0, authorFilter: curAuthorFilter, id: id0, kind: c0.kind || "", line: c0.line || 0,
            startLine: c0.start_line || 0, endLine: c0.end_line || 0, startIdx: c0.start_idx ?? "", endIdx: c0.end_idx ?? "",
            side: c0.side || "", unres: c0.unresolved !== false, collapsed: Boolean(c0.collapsed),
            msg: c0.message || "", msgsLen: msgsLen0, draftMode: c0.draft_mode || "",
            status: c0.status || "", reply: c0.reply || "", err: c0.error || "", sig: sig0
          };
          return sig0;
        }
      }
    }
    let sig = `${fileComments.length}:${curAuthorFilter}`;
    for (let i = 0; i < fileComments.length; i++) {
      const c = fileComments[i];
      if (!c) continue;
      const id = c.id || c.comment_id || "";
      const replyState = state.openReplyByItemId && state.openReplyByItemId[id];
      const editState = state.openDraftEditByItemId && state.openDraftEditByItemId[id];
      const replySig = replyState
        ? `R(${replyState.draft || ""}:${replyState.unresolved ? 1 : 0}:${replyState.submitting ? 1 : 0}:${replyState.saveStatus || ""}:${replyState.error || ""})`
        : "";
      const editSig = editState
        ? `E(${editState.tab || ""}:${editState.draft || ""}:${editState.unresolved ? 1 : 0}:${editState.saveStatus || ""})`
        : "";
      sig += `;${id}:${c.kind || ""}:${c.line || 0}:${c.start_line || 0}:${c.end_line || 0}:${c.start_idx ?? ""}:${c.end_idx ?? ""}:${c.side || ""}:${c.unresolved === false ? 0 : 1}:${c.collapsed ? 1 : 0}:${c.message || ""}:${Array.isArray(c.messages) ? c.messages.length : 0}:${c.draft_mode || ""}:${c.status || ""}:${c.reply || ""}:${c.error || ""}:${replySig}:${editSig}`;
    }
    return sig;
  }

  function getLineThreadIndicatorClass(fileComments, oldLn, newLn, startLn = null, lineIdx = null) {
    if (!Array.isArray(fileComments) || fileComments.length === 0) return "";
    let hasQuestion = false;
    let hasThread = false;
    for (const c of fileComments) {
      let inRange = false;
      if (c.start_idx != null && c.end_idx != null && lineIdx != null) {
        inRange = Number(lineIdx) >= Number(c.start_idx) && Number(lineIdx) <= Number(c.end_idx);
      } else {
        const sLn = Number(c.start_line || c.line || 0);
        const eLn = Number(c.end_line || c.line || 0);
        const checkLn = String(c.side || "REVISION").toUpperCase() === "PARENT"
          ? Number(oldLn || 0)
          : Number(newLn || startLn || 0);
        if (checkLn > 0 && sLn > 0 && eLn >= sLn) {
          inRange = checkLn >= sLn && checkLn <= eLn;
        }
      }
      if (inRange) {
        if (c.kind === "agent_question") {
          hasQuestion = true;
        } else {
          hasThread = true;
        }
      }
    }
    if (hasQuestion) return "code-line-has-question";
    if (hasThread) return "code-line-has-thread";
    return "";
  }

  function getPgLineCommentSummary(
    fileComments,
    targetLnStr,
    side = "right",
    lineIdx = null,
    filePath = null,
    startLn = null,
    endLn = null
  ) {
    if (!Array.isArray(fileComments) || fileComments.length === 0) return null;
    const targetLn = targetLnStr != null && String(targetLnStr).trim() !== "" ? Number(targetLnStr) : null;
    const isBase = side === "left" || String(side).toUpperCase() === "PARENT";

    const matched = fileComments.filter(c => {
      if (!c) return false;
      const cSide = String(c.side || "REVISION").toUpperCase();
      if (isBase && cSide !== "PARENT") return false;
      if (!isBase && cSide === "PARENT") return false;

      if (c.end_idx != null && lineIdx != null) {
        if (Number(c.end_idx) === Number(lineIdx)) return true;
        if (c.start_idx != null && Number(lineIdx) >= Number(c.start_idx) && Number(lineIdx) <= Number(c.end_idx)) {
          return true;
        }
      }
      if (filePath === "/COMMIT_MSG" && startLn != null && endLn != null) {
        const cLn = Number(c.line || c.end_line || c.start_line || 0);
        if (cLn >= Number(startLn) && cLn <= Number(endLn)) return true;
      }
      if (targetLn != null && !isNaN(targetLn) && targetLn > 0) {
        const sLn = Number(c.start_line || c.line || 0);
        const eLn = Number(c.end_line || c.line || 0);
        if (sLn > 0 && eLn >= sLn) {
          if (targetLn >= sLn && targetLn <= eLn) return true;
        } else {
          const cLn = Number(c.line || 0);
          if (cLn === targetLn) return true;
        }
      }
      return false;
    });

    if (matched.length === 0) return null;

    const drafts = matched.filter(c => c.kind === "draft");
    const questions = matched.filter(c => c.kind === "agent_question");
    const published = matched.filter(c => c.kind !== "draft" && c.kind !== "agent_question");
    const unresolved = published.filter(c => c.unresolved !== false);
    const resolved = published.filter(c => c.unresolved === false);
    const robots = matched.filter(c => Boolean(c.is_robot || c.robot_id || (typeof isPgRobotComment === "function" && isPgRobotComment(c))));

    return {
      count: matched.length,
      comments: matched,
      firstId: matched[0].id || matched[0].comment_id || "",
      hasDrafts: drafts.length > 0,
      hasQuestions: questions.length > 0,
      hasUnresolved: unresolved.length > 0,
      hasResolved: resolved.length > 0 && unresolved.length === 0 && drafts.length === 0,
      hasRobots: robots.length > 0,
      unresolvedCount: unresolved.length,
      draftCount: drafts.length,
      questionCount: questions.length,
      resolvedCount: resolved.length,
      robotCount: robots.length,
    };
  }

  function formatPgGutterCommentTooltip(summary, lineNum, isBase = false, filePath = "") {
    if (!summary) return "";
    const parts = [];
    const sideStr = isBase ? " (Base)" : "";
    const fileStr = filePath ? ` on ${filePath}` : "";
    const locStr = `Line ${lineNum}${sideStr}${fileStr}`;
    if (summary.hasUnresolved) {
      parts.push(`${summary.unresolvedCount} unresolved comment${summary.unresolvedCount > 1 ? "s" : ""}`);
    }
    if (summary.hasDrafts) {
      parts.push(`${summary.draftCount} draft${summary.draftCount > 1 ? "s" : ""}`);
    }
    if (summary.hasQuestions) {
      parts.push(`${summary.questionCount} agent question${summary.questionCount > 1 ? "s" : ""}`);
    }
    if (summary.hasResolved && !summary.hasUnresolved && !summary.hasDrafts) {
      parts.push(`${summary.resolvedCount} resolved comment${summary.resolvedCount > 1 ? "s" : ""}`);
    }
    if (summary.hasRobots && !summary.hasUnresolved && !summary.hasDrafts && !summary.hasQuestions) {
      parts.push(`${summary.robotCount} robot comment${summary.robotCount > 1 ? "s" : ""}`);
    }
    const desc = parts.length > 0 ? parts.join(", ") : `${summary.count} comment${summary.count > 1 ? "s" : ""}`;
    return `${locStr}: ${desc} · Click to toggle thread`;
  }

  function renderPgGutterCommentBadgeHtml(summary, clNum, filePath, side, lineNum) {
    if (!summary || summary.count === 0) return "";
    const isBase = side === "left" || String(side).toUpperCase() === "PARENT";
    const num = Number(clNum || 0);
    const safeFile = escapeHtml(String(filePath || ""));
    const firstId = escapeHtml(String(summary.firstId || ""));
    let badgeCls = "has-comment";
    let icon = "💬";

    if (summary.hasQuestions) {
      badgeCls = "has-question";
      icon = "🤖";
    } else if (summary.hasDrafts) {
      badgeCls = "has-draft";
      icon = "📝";
    } else if (summary.hasUnresolved) {
      badgeCls = "has-unresolved";
      icon = "💬";
    } else if (summary.hasRobots) {
      badgeCls = "has-robot";
      icon = "🤖";
    } else if (summary.hasResolved) {
      badgeCls = "is-resolved";
      icon = "✓";
    }

    const tooltip = escapeHtml(formatPgGutterCommentTooltip(summary, lineNum, isBase, filePath));
    const countHtml = summary.count > 1 ? `<span class="pg-gutter-comment-count">${summary.count}</span>` : "";

    return `<span class="pg-gutter-comment-badge ${badgeCls}" data-cl="${num}" data-file="${safeFile}" data-side="${isBase ? "left" : "right"}" data-line="${escapeHtml(String(lineNum))}" data-comment-id="${firstId}" title="${tooltip}" role="button" tabindex="0" aria-label="${tooltip}" onclick="onPgGutterCommentBadgeClick(event, ${num}, '${escapeJsQuote(filePath)}', '${escapeJsQuote(summary.firstId)}', this)" onkeydown="onPgGutterCommentBadgeKeyDown(event, ${num}, '${escapeJsQuote(filePath)}', '${escapeJsQuote(summary.firstId)}', this)"><span class="pg-gutter-comment-icon">${icon}</span>${countHtml}</span>`;
  }

  function onPgGutterCommentBadgeClick(event, clNum, filePath, commentId, badgeEl) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const safeFile = String(filePath || "");
    const idStr = String(commentId || "");
    const lineEl = badgeEl ? badgeEl.closest(".code-line") : null;
    const targetLine = badgeEl ? badgeEl.getAttribute("data-line") : null;
    const isBase = badgeEl ? badgeEl.getAttribute("data-side") === "left" : false;

    // Uncollapse file if file diff block is collapsed
    if (num && safeFile && typeof isPgFileCollapsed === "function" && isPgFileCollapsed(num, null, safeFile)) {
      if (typeof uncollapsePgFileDiff === "function") {
        uncollapsePgFileDiff(num, safeFile);
      }
    }

    // Uncollapse commit message panel if collapsed and file is /COMMIT_MSG
    if (safeFile === "/COMMIT_MSG" && typeof isCommitMsgCollapsed === "function" && isCommitMsgCollapsed(num)) {
      if (typeof uncollapseCommitMessage === "function") {
        uncollapseCommitMessage(num);
      }
    }

    let cardEl = idStr ? document.getElementById(`inlineItem-${idStr}`) : null;
    if (!cardEl && lineEl) {
      let next = lineEl.nextElementSibling;
      while (next && next.classList && next.classList.contains("diff-inline-comment")) {
        cardEl = next;
        break;
      }
    }

    if (cardEl) {
      const isNowCollapsed = cardEl.classList.contains("collapsed");
      const safeCardId = cardEl.getAttribute("data-inline-id") || idStr;
      toggleInlineItemCollapse(num, safeCardId);
      if (isNowCollapsed) {
        try {
          cardEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (_) {
          cardEl.scrollIntoView();
        }
        cardEl.classList.add("pg-comment-focused-flash");
        setTimeout(() => {
          if (cardEl && cardEl.classList) cardEl.classList.remove("pg-comment-focused-flash");
        }, 1600);
        if (typeof showToast === "function") {
          showToast(`Expanded comment thread on ${safeFile}:${targetLine || 1}`, 1200);
        }
      } else {
        if (typeof showToast === "function") {
          showToast(`Collapsed comment thread on ${safeFile}:${targetLine || 1}`, 1200);
        }
      }
    } else if (targetLine) {
      if (typeof scrollToPgCommentOrFile === "function") {
        scrollToPgCommentOrFile(num, safeFile, Number(targetLine), idStr || null, isBase);
      }
      if (typeof showToast === "function") {
        showToast(`Jumped to comment on ${safeFile}:${targetLine}`, 1200);
      }
    }
  }

  function onPgGutterCommentBadgeKeyDown(event, clNum, filePath, commentId, badgeEl) {
    if (event && (event.key === "Enter" || event.key === " ")) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      onPgGutterCommentBadgeClick(event, clNum, filePath, commentId, badgeEl);
    }
  }

  const CODE_SEARCH_BASE_HOST = "https://source.corp.google.com/h/googleplex-android";
  const GERRIT_MAGIC_FILES = new Set(["/COMMIT_MSG", "/MERGE_LIST", "/PATCHSET_LEVEL"]);

  function findChangeByNumber(clOrNum) {
    return findPgChangeByNumber(clOrNum);
  }

  function findChangeByRevision(rev) {
    return findPgChangeByRevision(rev);
  }

  const ANDROID_BRANCH_SUPERPROJECTS = {
    "emu-main-next": ["platform/superproject/emu-main-next", "emu-main-next"],
    "emu-main-dev": ["platform/superproject/emu-main-dev", "emu-main-dev"],
    "studio-main": ["platform/superproject/base", "studio-main"],
    "main": ["platform/superproject/main", "main"],
  };

  const ANDROID_PROJECT_BRANCH_RULES = {
    "platform/hardware/google/aemu": { allowed: ["emu-main-dev", "main"], fallback: "emu-main-next" },
    "platform/hardware/google/gfxstream": { allowed: ["main", "emu-main-dev"], fallback: "emu-main-next" },
    "platform/build/bazel": { allowed: ["studio-main", "main"], fallback: "emu-main-next" },
  };

  function resolveAndroidSuperproject(cleanProject, cleanBranch) {
    if (!cleanProject.startsWith("platform/")) return null;
    const sub = cleanProject.slice("platform/".length);
    if (cleanProject === "platform/hardware/generic/goldfish") {
      return [...ANDROID_BRANCH_SUPERPROJECTS["emu-main-next"], sub];
    }
    if (cleanProject === "platform/external/qemu") {
      if (cleanBranch.startsWith("emu-main-next")) {
        return [...ANDROID_BRANCH_SUPERPROJECTS["emu-main-next"], "third_party/qemu"];
      }
      const key = cleanBranch === "main" ? "main" : "emu-main-dev";
      return [...ANDROID_BRANCH_SUPERPROJECTS[key], sub];
    }
    const rule = ANDROID_PROJECT_BRANCH_RULES[cleanProject];
    const spKey = rule
      ? (rule.allowed.includes(cleanBranch) ? cleanBranch : rule.fallback)
      : cleanBranch;
    const sp = ANDROID_BRANCH_SUPERPROJECTS[spKey];
    return sp ? [sp[0], sp[1], sub] : null;
  }

  function resolveCodeSearchBaseUrl(project, branch) {
    const cleanProject = String(project || "").trim().replace(/^\/+|\/+$/g, "");
    if (!cleanProject) return "";
    const rawBranch = String(branch || "").trim();
    const cleanBranch = (rawBranch.startsWith("refs/heads/")
      ? rawBranch.slice("refs/heads/".length)
      : rawBranch) || "main";
    const sp = resolveAndroidSuperproject(cleanProject, cleanBranch);
    if (sp) {
      return `${CODE_SEARCH_BASE_HOST}/${sp[0]}/+/${sp[1]}:${sp[2]}`;
    }
    return `${CODE_SEARCH_BASE_HOST}/${cleanProject}/+/${encodeURIComponent(cleanBranch)}:`;
  }

  const _ASCII_REL_PATH_RE = /^[a-zA-Z0-9_./-]+$/;

  function buildCodeSearchFileUrl(clOrNum, filePath, lineNum = null) {
    const rawPath = String(filePath || "").trim();
    if (!rawPath || GERRIT_MAGIC_FILES.has(rawPath)) return "";
    const relPath = rawPath.charCodeAt(0) === 47 ? rawPath.replace(/^\/+/, "") : rawPath;
    if (!relPath || relPath === "dev/null") return "";
    const clObj = (typeof clOrNum === "object" && clOrNum !== null)
      ? clOrNum
      : findChangeByNumber(clOrNum);
    let baseUrl = "";
    if (clObj) {
      if (clObj.code_search_base_url) {
        baseUrl = clObj.code_search_base_url;
      } else {
        if (
          clObj._cachedResolvedCsProj !== clObj.project ||
          clObj._cachedResolvedCsBranch !== clObj.branch ||
          clObj._cachedResolvedCsBaseUrl === undefined
        ) {
          clObj._cachedResolvedCsProj = clObj.project;
          clObj._cachedResolvedCsBranch = clObj.branch;
          clObj._cachedResolvedCsBaseUrl = resolveCodeSearchBaseUrl(clObj.project, clObj.branch);
        }
        baseUrl = clObj._cachedResolvedCsBaseUrl;
      }
    }
    if (!baseUrl) return "";
    const encodedPath = (_ASCII_REL_PATH_RE.test(relPath) && !relPath.includes("//"))
      ? relPath
      : relPath.split("/").filter(Boolean).map(seg => encodeURIComponent(seg)).join("/");
    if (!encodedPath) return "";
    let url = baseUrl.endsWith(":") ? `${baseUrl}${encodedPath}` : `${baseUrl}/${encodedPath}`;
    const ln = Number(lineNum || 0);
    if (ln > 0) {
      url += `;l=${Math.floor(ln)}`;
    }
    return url;
  }

  function renderCodeSearchFileLinkHtml(clOrNum, filePath, displayLabel = null, lineNum = null) {
    const rawFilePath = String(filePath || "");
    const label = displayLabel != null ? String(displayLabel) : rawFilePath;
    const safeLabel = escapeHtml(label);
    const csUrl = buildCodeSearchFileUrl(clOrNum, rawFilePath, lineNum);
    if (!csUrl) {
      return `<span>${safeLabel}</span>`;
    }
    const safeFilePath = displayLabel != null ? escapeHtml(rawFilePath) : safeLabel;
    const ln = Number(lineNum || 0);
    const lineHint = ln > 0 ? ` (line ${Math.floor(ln)})` : "";
    return `<a class="file-cs-link" href="${escapeHtml(csUrl)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()" title="Open ${safeFilePath}${lineHint} in Code Search ↗">${safeLabel}<span class="file-cs-link-icon" aria-hidden="true">↗</span></a>`;
  }

  const DIFF_COLOR_SCHEMES = [
    { id: "polygerrit_native", label: "🏛️ PolyGerrit Authentic" },
    { id: "syntax_intraline", label: "🎨 Syntax + Word Diff" },
    { id: "monokai", label: "⚡ Monokai Dark" },
    { id: "solarized_dark", label: "☀️ Solarized Dark" },
    { id: "solarized_light", label: "🌤️ Solarized Light" },
    { id: "github_light", label: "💡 GitHub Light" },
    { id: "github_high_contrast", label: "🌙 GitHub High-Contrast" },
    { id: "intraline_focus", label: "🔍 Word Diff Only" },
    { id: "semantic_review", label: "🛡️ Review Focus" },
    { id: "classic", label: "🌿 Classic Gerrit Tint" },
  ];

  const DIFF_SYNTAX_KEYWORDS = new Set([
    "if", "else", "elif", "for", "while", "do", "switch", "case", "default",
    "break", "continue", "return", "co_await", "co_return", "co_yield", "goto",
    "def", "class", "fn", "pub", "impl", "struct", "enum", "union", "trait",
    "let", "mut", "const", "constexpr", "consteval", "constinit", "static", "inline", "extern",
    "virtual", "override", "final", "explicit", "friend", "operator", "noexcept",
    "namespace", "using", "template", "typename", "concept", "requires",
    "import", "from", "as", "async", "await", "try", "catch", "except", "finally",
    "raise", "throw", "with", "yield", "lambda", "pass", "del", "in", "is", "not",
    "and", "or", "func", "package", "defer", "go", "select", "interface", "type",
    "var", "function", "export", "new", "delete", "sizeof", "alignof", "alignas", "static_assert",
    "decltype", "thread_local", "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast",
    "private", "protected", "public", "match", "where", "ref", "move", "unsafe",
    "crate", "mod", "dyn", "loop", "assert", "global", "nonlocal", "range", "fallthrough", "chan",
    "load", "rule", "provider", "aspect", "depset", "fail", "exports_files", "licenses", "alias", "subrules", "Label",
  ]);

  const DIFF_SYNTAX_TYPES_AND_CONSTS = new Set([
    "void", "bool", "char", "short", "int", "long", "float", "double", "signed",
    "unsigned", "auto", "size_t", "ssize_t", "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t", "uintptr_t", "intptr_t", "ptrdiff_t",
    "char8_t", "char16_t", "char32_t", "wchar_t", "nullptr_t", "intmax_t", "uintmax_t",
    "string", "string_view", "wstring", "u8string", "vector", "map", "unordered_map",
    "set", "unordered_set", "optional", "variant", "span", "unique_ptr", "shared_ptr",
    "weak_ptr", "Status", "StatusOr", "StatusCode", "Path", "Any", "Optional", "Union",
    "Dict", "List", "Tuple", "Set", "Callable", "Iterable", "Sequence", "str", "bytes",
    "dict", "list", "tuple", "frozenset", "object", "Exception", "RuntimeError",
    "ValueError", "TypeError", "OSError", "KeyError", "IndexError", "true", "false",
    "True", "False", "None", "null", "nullptr", "undefined", "NaN", "Infinity", "nil",
    "self", "this", "cls", "super", "Self", "Result", "Option", "Some", "Ok", "Err",
    "String", "Vec", "Box", "Arc", "Rc", "Mutex", "RwLock",
    "i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128", "usize", "f32", "f64",
    "byte", "rune", "any", "error", "complex64", "complex128", "uint",
    "isinstance", "issubclass", "len", "range", "enumerate", "zip", "print", "iter", "next", "repr", "min", "max", "sum",
    "make", "cap", "append", "copy", "close", "panic", "recover",
  ]);

  const DIFF_REVIEW_FLOW_TOKENS = new Set([
    "return", "co_return", "break", "continue", "goto", "throw", "raise", "panic",
    "CHECK", "DCHECK", "QCHECK", "LOG", "FATAL", "DFATAL", "assert", "exit", "abort",
  ]);

  const DIFF_REVIEW_SAFETY_TOKENS = new Set([
    "mutex", "Mutex", "lock_guard", "unique_lock", "shared_lock", "scoped_lock",
    "Lock", "RLock", "Condition", "Semaphore", "atomic", "Atomic", "thread", "Thread",
    "new", "delete", "free", "malloc", "calloc", "realloc", "memcpy", "memmove",
    "memset", "strcpy", "strncpy", "reinterpret_cast", "const_cast", "static_cast",
    "dynamic_cast", "move", "forward", "unsafe", "raw_ptr", "Send", "Sync", "detach",
  ]);

  const DIFF_REVIEW_STATUS_TOKENS = new Set([
    "StatusOr", "Status", "StatusCode", "absl", "Error", "Exception", "RuntimeError",
    "ValueError", "OSError", "TimeoutExpired", "ok", "try", "catch", "except",
    "finally", "errno", "perror", "strerror",
  ]);

  const MAX_INTRALINE_PAIR_RUN = 14;
  const MAX_INTRALINE_LINE_LEN = 420;
  const MAX_INTRALINE_LCS_TOKENS = 48;

  function setDiffColorScheme(schemeId, showNotification = false) {
    if (!DIFF_COLOR_SCHEMES.some(s => s.id === schemeId)) return;
    state.diffColorScheme = schemeId;
    try {
      window.localStorage.setItem(DIFF_COLOR_SCHEME_STORAGE_KEY, schemeId);
    } catch (_e) {}
    const prefs = getPgDiffPreferences();
    if (prefs && prefs.colorScheme !== schemeId) {
      prefs.colorScheme = schemeId;
      savePgDiffPreferences(prefs);
    }
    const sel = document.getElementById("diffColorSchemeSelect");
    if (sel && sel.value !== schemeId) sel.value = schemeId;
    const prefSel = document.getElementById("pgDiffPrefColorScheme");
    if (prefSel && prefSel.value !== schemeId) prefSel.value = schemeId;
    updatePgThemePreview(schemeId);
    renderDiffPane({ preserveScroll: true });
    if (showNotification) {
      const found = DIFF_COLOR_SCHEMES.find(s => s.id === schemeId);
      showToast(`Diff Scheme: ${found ? found.label : schemeId} (Shortcut: Alt+C)`);
    }
  }

  function cycleDiffColorScheme() {
    const curIdx = DIFF_COLOR_SCHEMES.findIndex(s => s.id === state.diffColorScheme);
    const nextIdx = (curIdx + 1) % DIFF_COLOR_SCHEMES.length;
    setDiffColorScheme(DIFF_COLOR_SCHEMES[nextIdx].id, true);
  }

  function renderDiffColorSchemeSelectHtml() {
    const optsHtml = DIFF_COLOR_SCHEMES.map(s => {
      const selAttr = s.id === state.diffColorScheme ? " selected" : "";
      return `<option value="${escapeHtml(s.id)}"${selAttr}>${escapeHtml(s.label)}</option>`;
    }).join("");
    return `<select
      id="diffColorSchemeSelect"
      class="diff-scheme-select"
      onchange="setDiffColorScheme(this.value, true)"
      onclick="event.stopPropagation()"
      title="Switch code diff color scheme (Shortcut: Alt+C)"
      aria-label="Code diff color scheme"
    >${optsHtml}</select>`;
  }

  const _DIFF_LANG_BAZEL = Object.freeze({ id: "bazel", badge: "Bazel" });
  const _DIFF_LANG_CMAKE = Object.freeze({ id: "config", badge: "CMake" });
  const _DIFF_LANG_CPP = Object.freeze({ id: "cpp", badge: "C++" });
  const _DIFF_LANG_PYTHON = Object.freeze({ id: "python", badge: "Python" });
  const _DIFF_LANG_RUST = Object.freeze({ id: "rust", badge: "Rust" });
  const _DIFF_LANG_GO = Object.freeze({ id: "go", badge: "Go" });
  const _DIFF_LANG_JS = Object.freeze({ id: "js", badge: "JS" });
  const _DIFF_LANG_TS = Object.freeze({ id: "js", badge: "TS" });
  const _DIFF_LANG_JAVA = Object.freeze({ id: "cpp", badge: "Java" });
  const _DIFF_LANG_KOTLIN = Object.freeze({ id: "cpp", badge: "Kotlin" });
  const _DIFF_LANG_SHELL = Object.freeze({ id: "config", badge: "Shell" });
  const _DIFF_LANG_PROTO = Object.freeze({ id: "config", badge: "Proto" });
  const _DIFF_LANG_GENERIC = Object.freeze({ id: "generic", badge: "" });
  const _DIFF_LANG_BY_EXT = new Map([
    ["bzl", _DIFF_LANG_BAZEL],
    ["bazel", _DIFF_LANG_BAZEL],
    ["cmake", _DIFF_LANG_CMAKE],
    ["c", _DIFF_LANG_CPP],
    ["cc", _DIFF_LANG_CPP],
    ["cpp", _DIFF_LANG_CPP],
    ["cxx", _DIFF_LANG_CPP],
    ["h", _DIFF_LANG_CPP],
    ["hh", _DIFF_LANG_CPP],
    ["hpp", _DIFF_LANG_CPP],
    ["hxx", _DIFF_LANG_CPP],
    ["inc", _DIFF_LANG_CPP],
    ["inl", _DIFF_LANG_CPP],
    ["m", _DIFF_LANG_CPP],
    ["mm", _DIFF_LANG_CPP],
    ["py", _DIFF_LANG_PYTHON],
    ["pyi", _DIFF_LANG_PYTHON],
    ["rs", _DIFF_LANG_RUST],
    ["go", _DIFF_LANG_GO],
    ["js", _DIFF_LANG_JS],
    ["mjs", _DIFF_LANG_JS],
    ["cjs", _DIFF_LANG_JS],
    ["jsx", _DIFF_LANG_JS],
    ["ts", _DIFF_LANG_TS],
    ["tsx", _DIFF_LANG_TS],
    ["java", _DIFF_LANG_JAVA],
    ["kt", _DIFF_LANG_KOTLIN],
    ["kts", _DIFF_LANG_KOTLIN],
    ["html", Object.freeze({ id: "markup", badge: "HTML" })],
    ["htm", Object.freeze({ id: "markup", badge: "HTM" })],
    ["xml", Object.freeze({ id: "markup", badge: "XML" })],
    ["svg", Object.freeze({ id: "markup", badge: "SVG" })],
    ["json", Object.freeze({ id: "config", badge: "JSON" })],
    ["yaml", Object.freeze({ id: "config", badge: "YAML" })],
    ["yml", Object.freeze({ id: "config", badge: "YML" })],
    ["toml", Object.freeze({ id: "config", badge: "TOML" })],
    ["ini", Object.freeze({ id: "config", badge: "INI" })],
    ["cfg", Object.freeze({ id: "config", badge: "CFG" })],
    ["conf", Object.freeze({ id: "config", badge: "CONF" })],
    ["properties", Object.freeze({ id: "config", badge: "PROPERTIES" })],
    ["textproto", Object.freeze({ id: "config", badge: "TEXTPROTO" })],
    ["pbtxt", Object.freeze({ id: "config", badge: "PBTXT" })],
    ["proto", _DIFF_LANG_PROTO],
    ["sh", _DIFF_LANG_SHELL],
    ["bash", _DIFF_LANG_SHELL],
    ["zsh", _DIFF_LANG_SHELL],
    ["md", Object.freeze({ id: "generic", badge: "MD" })],
    ["rst", Object.freeze({ id: "generic", badge: "RST" })],
    ["txt", Object.freeze({ id: "generic", badge: "TXT" })],
  ]);

  function detectDiffFileLanguage(filePath) {
    if (!filePath) return _DIFF_LANG_GENERIC;
    const raw = typeof filePath === "string" ? filePath.trim() : String(filePath).trim();
    const slashIdx = raw.lastIndexOf("/");
    const base = slashIdx >= 0 ? raw.slice(slashIdx + 1) : raw;
    const lowerBase = base.toLowerCase();
    if (lowerBase === "build" || lowerBase === "build.bazel" || lowerBase === "workspace") {
      return _DIFF_LANG_BAZEL;
    }
    if (lowerBase === "cmakelists.txt") {
      return _DIFF_LANG_CMAKE;
    }
    const dotIdx = lowerBase.lastIndexOf(".");
    if (dotIdx < 0) return _DIFF_LANG_GENERIC;
    const ext = lowerBase.slice(dotIdx + 1);
    return _DIFF_LANG_BY_EXT.get(ext) || _DIFF_LANG_GENERIC;
  }

  const _DIFF_TOKEN_SPLIT_RE = /[A-Za-z0-9_\u0080-\uffff]+|\s+|[^\sA-Za-z0-9_\u0080-\uffff]/g;

  function splitIntoDiffTokens(str) {
    const out = [];
    if (!str) return out;
    _DIFF_TOKEN_SPLIT_RE.lastIndex = 0;
    let m;
    while ((m = _DIFF_TOKEN_SPLIT_RE.exec(str)) !== null) {
      out.push({ text: m[0], start: m.index, end: m.index + m[0].length });
    }
    return out;
  }

  function mergeIntralineIntervals(intervals) {
    if (!intervals.length) return [];
    const sorted = intervals.slice().sort((a, b) => a[0] - b[0]);
    const merged = [sorted[0].slice()];
    for (let i = 1; i < sorted.length; i++) {
      const prev = merged[merged.length - 1];
      if (sorted[i][0] <= prev[1]) {
        prev[1] = Math.max(prev[1], sorted[i][1]);
      } else {
        merged.push(sorted[i].slice());
      }
    }
    return merged;
  }

  const DIFF_SUBTOKEN_REGEX = /[A-Z]+(?=[A-Z][a-z]|[0-9]|\b)|[A-Z]?[a-z\u00df-\uffff]+|[0-9]+|[\uD800-\uDBFF][\uDC00-\uDFFF]|[\u0080-\uffff]|[\s]+|[_\-.:/\\\\]+|[^\s\w]/g;

  function splitDiffSubtokens(text) {
    const tokens = [];
    if (!text) return tokens;
    DIFF_SUBTOKEN_REGEX.lastIndex = 0;
    let m;
    while ((m = DIFF_SUBTOKEN_REGEX.exec(text)) !== null) {
      tokens.push({ text: m[0], start: m.index, end: m.index + m[0].length });
    }
    return tokens;
  }

  function refineIntralineTokenPair(dTok, aTok, delOffset = 0, addOffset = 0) {
    const dText = String((dTok && dTok.text) || "");
    const aText = String((aTok && aTok.text) || "");
    if (!dText || !aText || dText === aText) return null;

    const dSubs = splitDiffSubtokens(dText);
    const aSubs = splitDiffSubtokens(aText);
    if (!dSubs.length || !aSubs.length) {
      return {
        delSpans: [[dTok.start + delOffset, dTok.end + delOffset]],
        addSpans: [[aTok.start + addOffset, aTok.end + addOffset]],
      };
    }

    let p = 0;
    const minLen = Math.min(dSubs.length, aSubs.length);
    while (p < minLen && dSubs[p].text === aSubs[p].text) {
      p++;
    }

    let s = 0;
    while (
      s < minLen - p &&
      dSubs[dSubs.length - 1 - s].text === aSubs[aSubs.length - 1 - s].text
    ) {
      s++;
    }

    if (p === 0 && s === 0) {
      return {
        delSpans: [[dTok.start + delOffset, dTok.end + delOffset]],
        addSpans: [[aTok.start + addOffset, aTok.end + addOffset]],
      };
    }

    const dMid = dSubs.slice(p, dSubs.length - s);
    const aMid = aSubs.slice(p, aSubs.length - s);

    const delSpans = dMid.length
      ? [[dTok.start + delOffset + dMid[0].start, dTok.start + delOffset + dMid[dMid.length - 1].end]]
      : [];
    const addSpans = aMid.length
      ? [[aTok.start + addOffset + aMid[0].start, aTok.start + addOffset + aMid[aMid.length - 1].end]]
      : [];

    return { delSpans, addSpans };
  }

  const _pgPairIntralineCache = new Map();
  const PG_PAIR_INTRALINE_CACHE_MAX = 4096;
  let _pgIntralineLcsDpBuffer = new Uint16Array((MAX_INTRALINE_LCS_TOKENS + 1) * (MAX_INTRALINE_LCS_TOKENS + 1));

  function computePairIntralineIntervals(delRaw, addRaw) {
    const delCh0 = delRaw ? delRaw.charCodeAt(0) : 0;
    const addCh0 = addRaw ? addRaw.charCodeAt(0) : 0;
    const delOffset = (delCh0 === 43 || delCh0 === 45 || delCh0 === 32) ? 1 : 0;
    const addOffset = (addCh0 === 43 || addCh0 === 45 || addCh0 === 32) ? 1 : 0;
    const delBody = delOffset ? delRaw.slice(1) : delRaw;
    const addBody = addOffset ? addRaw.slice(1) : addRaw;
    if (!delBody || !addBody || delBody === addBody) return null;
    if (delBody.length > MAX_INTRALINE_LINE_LEN || addBody.length > MAX_INTRALINE_LINE_LEN) return null;

    const cacheKey = `${delOffset}${addOffset}\x00${delBody}\x00${addBody}`;
    const cachedPair = _pgPairIntralineCache.get(cacheKey);
    if (cachedPair !== undefined) return cachedPair;

    const dToks = splitIntoDiffTokens(delBody);
    const aToks = splitIntoDiffTokens(addBody);
    if (!dToks.length || !aToks.length) {
      if (_pgPairIntralineCache.size >= PG_PAIR_INTRALINE_CACHE_MAX) _pgPairIntralineCache.clear();
      _pgPairIntralineCache.set(cacheKey, null);
      return null;
    }

    let p = 0;
    const minLen = Math.min(dToks.length, aToks.length);
    while (p < minLen && dToks[p].text === aToks[p].text) p++;

    let s = 0;
    while (
      s < minLen - p &&
      dToks[dToks.length - 1 - s].text === aToks[aToks.length - 1 - s].text
    ) {
      s++;
    }
    if (p === 0 && s === 0) {
      if (_pgPairIntralineCache.size >= PG_PAIR_INTRALINE_CACHE_MAX) _pgPairIntralineCache.clear();
      _pgPairIntralineCache.set(cacheKey, null);
      return null;
    }

    const dMid = dToks.slice(p, dToks.length - s);
    const aMid = aToks.slice(p, aToks.length - s);
    const dLen = dMid.length;
    const aLen = aMid.length;
    let outRes = null;

    if (dLen === 1 && aLen === 1) {
      const refined = refineIntralineTokenPair(dMid[0], aMid[0], delOffset, addOffset);
      if (refined) {
        outRes = {
          delSpans: mergeIntralineIntervals(refined.delSpans),
          addSpans: mergeIntralineIntervals(refined.addSpans),
        };
      }
    } else if (dLen > 0 && dLen <= MAX_INTRALINE_LCS_TOKENS && aLen > 0 && aLen <= MAX_INTRALINE_LCS_TOKENS) {
      let canUseIsolatedFastPath = dLen === aLen;
      if (canUseIsolatedFastPath) {
        for (let k = 0; k < dLen - 1; k++) {
          if (dMid[k].text !== aMid[k].text && dMid[k + 1].text !== aMid[k + 1].text) {
            canUseIsolatedFastPath = false;
            break;
          }
        }
      }
      if (canUseIsolatedFastPath) {
        const allDelSpans = [];
        const allAddSpans = [];
        for (let k = 0; k < dLen; k++) {
          if (dMid[k].text === aMid[k].text) continue;
          const refined = refineIntralineTokenPair(dMid[k], aMid[k], delOffset, addOffset);
          if (refined) {
            allDelSpans.push(...refined.delSpans);
            allAddSpans.push(...refined.addSpans);
          } else {
            allDelSpans.push([dMid[k].start + delOffset, dMid[k].end + delOffset]);
            allAddSpans.push([aMid[k].start + addOffset, aMid[k].end + addOffset]);
          }
        }
        outRes = {
          delSpans: mergeIntralineIntervals(allDelSpans),
          addSpans: mergeIntralineIntervals(allAddSpans),
        };
      } else {
        const stride = aLen + 1;
        const needed = (dLen + 1) * stride;
        if (_pgIntralineLcsDpBuffer.length < needed) {
          _pgIntralineLcsDpBuffer = new Uint16Array(needed);
        }
        const dp = _pgIntralineLcsDpBuffer;
        for (let j = 0; j <= aLen; j++) dp[dLen * stride + j] = 0;
        for (let i = dLen - 1; i >= 0; i--) {
          const rowOff = i * stride;
          const nextRowOff = (i + 1) * stride;
          dp[rowOff + aLen] = 0;
          const dText = dMid[i].text;
          for (let j = aLen - 1; j >= 0; j--) {
            if (dText === aMid[j].text) {
              dp[rowOff + j] = dp[nextRowOff + j + 1] + 1;
            } else {
              const down = dp[nextRowOff + j];
              const right = dp[rowOff + j + 1];
              dp[rowOff + j] = down >= right ? down : right;
            }
          }
        }
        const matchedPairs = [];
        let i = 0;
        let j = 0;
        while (i < dLen && j < aLen) {
          if (dMid[i].text === aMid[j].text) {
            matchedPairs.push([i, j]);
            i++;
            j++;
          } else if (dp[(i + 1) * stride + j] >= dp[i * stride + j + 1]) {
            i++;
          } else {
            j++;
          }
        }

        const allDelSpans = [];
        const allAddSpans = [];
        let dPrev = 0;
        let aPrev = 0;

        for (let k = 0; k <= matchedPairs.length; k++) {
          const dEnd = k < matchedPairs.length ? matchedPairs[k][0] : dLen;
          const aEnd = k < matchedPairs.length ? matchedPairs[k][1] : aLen;
          const dChunk = dMid.slice(dPrev, dEnd);
          const aChunk = aMid.slice(aPrev, aEnd);

          if (dChunk.length === 1 && aChunk.length === 1) {
            const refined = refineIntralineTokenPair(dChunk[0], aChunk[0], delOffset, addOffset);
            if (refined) {
              allDelSpans.push(...refined.delSpans);
              allAddSpans.push(...refined.addSpans);
            } else {
              allDelSpans.push([dChunk[0].start + delOffset, dChunk[0].end + delOffset]);
              allAddSpans.push([aChunk[0].start + addOffset, aChunk[0].end + addOffset]);
            }
          } else {
            dChunk.forEach(t => allDelSpans.push([t.start + delOffset, t.end + delOffset]));
            aChunk.forEach(t => allAddSpans.push([t.start + addOffset, t.end + addOffset]));
          }

          if (k < matchedPairs.length) {
            dPrev = matchedPairs[k][0] + 1;
            aPrev = matchedPairs[k][1] + 1;
          }
        }

        outRes = {
          delSpans: mergeIntralineIntervals(allDelSpans),
          addSpans: mergeIntralineIntervals(allAddSpans),
        };
      }
    }

    if (!outRes) {
      const delSpans = dLen
        ? [[dMid[0].start + delOffset, dMid[dLen - 1].end + delOffset]]
        : [];
      const addSpans = aLen
        ? [[aMid[0].start + addOffset, aMid[aLen - 1].end + addOffset]]
        : [];
      outRes = { delSpans, addSpans };
    }

    if (_pgPairIntralineCache.size >= PG_PAIR_INTRALINE_CACHE_MAX) {
      _pgPairIntralineCache.clear();
    }
    _pgPairIntralineCache.set(cacheKey, outRes);
    return outRes;
  }

  function computeFileIntralineRanges(lines) {
    if (!Array.isArray(lines) || lines.length === 0) return {};
    const firstLn = lines[0];
    const lastLn = lines[lines.length - 1];
    const cacheVer = `${lines.length}:${firstLn ? firstLn.text : ""}:${lastLn ? lastLn.text : ""}`;
    if (lines._cachedIntralineVer === cacheVer && lines._cachedIntralineMap) {
      return lines._cachedIntralineMap;
    }
    const map = {};
    let i = 0;
    while (i < lines.length) {
      if (lines[i].type !== "del") {
        i++;
        continue;
      }
      const delStart = i;
      while (i < lines.length && lines[i].type === "del") i++;
      const delEnd = i;
      const addStart = i;
      while (i < lines.length && lines[i].type === "add") i++;
      const addEnd = i;

      const delCount = delEnd - delStart;
      const addCount = addEnd - addStart;
      if (delCount > 0 && addCount > 0 && delCount <= MAX_INTRALINE_PAIR_RUN && addCount <= MAX_INTRALINE_PAIR_RUN) {
        const pairCount = Math.min(delCount, addCount);
        for (let k = 0; k < pairCount; k++) {
          const dIdx = delStart + k;
          const aIdx = addStart + k;
          const paired = computePairIntralineIntervals(
            String(lines[dIdx].text || ""),
            String(lines[aIdx].text || "")
          );
          if (paired) {
            if (paired.delSpans.length) map[dIdx] = paired.delSpans;
            if (paired.addSpans.length) map[aIdx] = paired.addSpans;
          }
        }
      }
    }
    lines._cachedIntralineVer = cacheVer;
    lines._cachedIntralineMap = map;
    return map;
  }

  const _UPPER_CONST_IDENT_RE = /^[A-Z][A-Z0-9_]{2,}$/;
  const _PASCAL_IDENT_RE = /^[A-Z][A-Za-z0-9_]*$/;
  const _PY_ASSIGN_CTX_RE = /^\s*=(?!=)/;
  const _CPP_PREPROC_DIR_RE = /^#\s*(?:include|define|ifdef|ifndef|endif|if|elif|else|pragma|undef|error|warning|line)\b/;
  const _CPP_PREPROC_HEAD_RE = /^#\s*[a-zA-Z_]+/;
  const _CPP_INCLUDE_ANGLE_RE = /^(\s*)(<[^>\n\r]+>)/;
  const _DIFF_CODE_LINE_TOKEN_SOURCE = [
    "(\\/\\/.*$|\\/\\*[\\s\\S]*?\\*\\/|<!--[\\s\\S]*?-->)",
    "|(#.*$)",
    '|("(?:\\\\.|[^"\\\\])*"|\'(?:\\\\.|[^\'\\\\])*\'|`(?:\\\\.|[^`\\\\])*`)',
    "|(@[A-Za-z_][A-Za-z0-9_.]*)",
    "|(\\b(?:0[xX][0-9a-fA-F_]+|\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?[uUlLfF]*)\\b)",
    "|([A-Za-z_\\u0080-\\uffff][A-Za-z0-9_\\u0080-\\uffff]*)",
    "|(::|->|=>|===|!==|==|!=|<=|>=|&&|\\|\\||\\+=|-=|\\*=|\\/=|%=|&=|\\|=|\\^=|<<|>>|\\.\\.\\.)",
  ].join("");
  const _DIFF_CODE_LINE_TOKEN_RE = new RegExp(_DIFF_CODE_LINE_TOKEN_SOURCE, "g");

  const DIFF_SYNTAX_IDENT_CLASS_MAP = new Map();
  DIFF_SYNTAX_TYPES_AND_CONSTS.forEach(t => DIFF_SYNTAX_IDENT_CLASS_MAP.set(t, "tok-type"));
  DIFF_SYNTAX_KEYWORDS.forEach(k => DIFF_SYNTAX_IDENT_CLASS_MAP.set(k, "tok-keyword"));
  const _IDENT_LAST_CHAR_MASK = new Uint32Array(17 * 128);
  DIFF_SYNTAX_IDENT_CLASS_MAP.forEach((_cls, kw) => {
    const kwLen = kw.length;
    const kwC0 = kw.charCodeAt(0);
    if (kwLen >= 2 && kwLen <= 16 && kwC0 < 128) {
      _IDENT_LAST_CHAR_MASK[(kwLen << 7) | kwC0] |= (1 << (kw.charCodeAt(kwLen - 1) & 31));
    }
  });
  const _CANONICAL_CPP_HL_PREFIX = "cpp\x00syntax_intraline\x00011\x00";
  const _HTML_NON_QUOTE_ESCAPE_RE = /[&<>']/;

  function classifyIdentifierToken(ident, nextContext, scheme, langId, bodyStr, mEndIdx) {
    if (scheme === "semantic_review") {
      if (DIFF_REVIEW_FLOW_TOKENS.has(ident)) return "tok-rev-flow";
      if (DIFF_REVIEW_SAFETY_TOKENS.has(ident)) return "tok-rev-safety";
      if (DIFF_REVIEW_STATUS_TOKENS.has(ident) || ident.startsWith("UV_") || ident.endsWith("Error")) {
        return "tok-rev-status";
      }
      return "";
    }
    if (scheme === "intraline_focus") return "";
    const len = ident.length;
    const c0 = ident.charCodeAt(0);
    if (len >= 2 && len <= 16 && c0 < 128) {
      if ((_IDENT_LAST_CHAR_MASK[(len << 7) | c0] & (1 << (ident.charCodeAt(len - 1) & 31))) !== 0) {
        const knownCls = DIFF_SYNTAX_IDENT_CLASS_MAP.get(ident);
        if (knownCls !== undefined) return knownCls;
      }
    }
    const hasBody = bodyStr !== undefined && mEndIdx !== undefined;
    const cNext0 = hasBody
      ? bodyStr.charCodeAt(mEndIdx)
      : (nextContext ? String(nextContext).charCodeAt(0) : 0);
    if (cNext0 === 40 && !(c0 >= 65 && c0 <= 90)) return "tok-func";
    if (cNext0 === 58) {
      const cNext1 = hasBody
        ? bodyStr.charCodeAt(mEndIdx + 1)
        : (nextContext ? String(nextContext).charCodeAt(1) : 0);
      if (cNext1 === 58) return "tok-type";
    } else if (cNext0 === 33) {
      return "tok-macro";
    }
    if (langId === "bazel" || langId === "python") {
      if (!hasBody || cNext0 === 61 || cNext0 === 32 || cNext0 === 9) {
        const ctx = hasBody ? bodyStr.slice(mEndIdx, mEndIdx + 8) : String(nextContext || "");
        if (_PY_ASSIGN_CTX_RE.test(ctx)) {
          return "tok-attr";
        }
      }
    }
    if (c0 >= 65 && c0 <= 90) {
      if (langId !== "markup") {
        if (_PASCAL_IDENT_RE.test(ident)) return "tok-type";
      } else if (_UPPER_CONST_IDENT_RE.test(ident)) {
        return "tok-type";
      }
    }
    if (cNext0 === 40) return "tok-func";
    return "";
  }

  const _PG_BUG_REF_RE = /\bb\/(\d{6,12})\b/gi;
  const _PG_TAB_MARKER_HTML = '<span class="pg-tab-marker" title="Tab (\\t)"><span class="pg-tab-arrow">→</span>\t</span>';
  const PG_HIGHLIGHTED_LINE_CACHE_MAX = 8192;
  const _pgHighlightedLineHtmlCache = new Map();
  const _DIFF_CODE_LINE_FAST_RE = new RegExp(
    _DIFF_CODE_LINE_TOKEN_SOURCE.replace(/\((?!\?:)/g, "(?:"),
    "g"
  );

  function decorateDiffLineSlice(sliceText, isTrailing, hasTab, hasBugRef) {
    let escaped = escapeHtml(sliceText);
    if (hasTab && escaped.indexOf("\t") !== -1) {
      escaped = escaped.replace(/\t/g, _PG_TAB_MARKER_HTML);
    }
    if (!isTrailing && hasBugRef && (escaped.indexOf("b/") !== -1 || escaped.indexOf("B/") !== -1)) {
      escaped = escaped.replace(
        _PG_BUG_REF_RE,
        '<span class="pg-diff-bug-ref" data-bug-id="$1" onmouseenter="showPgBugHovercard(event, \'$1\')" onmouseleave="scheduleHidePgBugHovercard()">b/$1</span>'
      );
    }
    if (isTrailing && sliceText.length > 0) {
      return `<span class="pg-ws-error pg-trailing-ws" title="Trailing whitespace error">${escaped}</span>`;
    }
    return escaped;
  }

  function tokenizeDiffCodeLine(rawLine, langId, scheme) {
    const text = String(rawLine || "");
    if (!text) return [];
    const spans = [];
    let offset = 0;
    const ch0 = text.charCodeAt(0);
    if (ch0 === 43 || ch0 === 45 || ch0 === 32) {
      spans.push({ start: 0, end: 1, cls: "tok-prefix" });
      offset = 1;
    }
    if (offset >= text.length) return spans;

    if (scheme === "intraline_focus") {
      const isHashComment = langId === "python" || langId === "bazel" || langId === "config";
      const commentIdx = isHashComment ? text.indexOf("#", offset) : text.indexOf("//", offset);
      if (commentIdx === offset || (commentIdx > offset && text.charCodeAt(commentIdx - 1) <= 32)) {
        spans.push({ start: commentIdx, end: text.length, cls: "tok-comment" });
      }
      return spans;
    }

    const tokenRe = _DIFF_CODE_LINE_FAST_RE;
    tokenRe.lastIndex = offset;

    let match;
    while ((match = tokenRe.exec(text)) !== null) {
      const mStart = match.index;
      const tokStr = match[0];
      const mEnd = mStart + tokStr.length;
      const c0 = tokStr.charCodeAt(0);
      let tokCls = "";
      if ((c0 >= 65 && c0 <= 90) || (c0 >= 97 && c0 <= 122) || c0 === 95 || c0 >= 128) {
        tokCls = classifyIdentifierToken(tokStr, null, scheme, langId, text, mEnd);
      } else if (c0 >= 48 && c0 <= 57) {
        tokCls = scheme === "semantic_review" ? "" : "tok-number";
      } else if (c0 === 34 || c0 === 39 || c0 === 96) {
        tokCls = scheme === "semantic_review" ? "" : "tok-string";
      } else if (c0 === 47) {
        const c1 = tokStr.charCodeAt(1);
        if (c1 === 47 || c1 === 42) {
          tokCls = "tok-comment";
        } else {
          tokCls = scheme === "semantic_review" ? "" : "tok-op";
        }
      } else if (c0 === 35) {
        if (langId === "cpp" && _CPP_PREPROC_DIR_RE.test(tokStr) && isLeadingWhitespaceOnly(text, offset, mStart)) {
          const dirMatch = tokStr.match(_CPP_PREPROC_HEAD_RE);
          const dirStr = dirMatch ? dirMatch[0] : "#";
          const dirLen = dirStr.length;
          spans.push({ start: mStart, end: mStart + dirLen, cls: "tok-macro" });
          const restOfDirective = tokStr.slice(dirLen);
          const headerAngleMatch = restOfDirective.match(_CPP_INCLUDE_ANGLE_RE);
          if (dirStr.indexOf("include") !== -1 && headerAngleMatch) {
            const wsLen = headerAngleMatch[1].length;
            const hLen = headerAngleMatch[2].length;
            const hStart = mStart + dirLen + wsLen;
            spans.push({ start: hStart, end: hStart + hLen, cls: "tok-string" });
            tokenRe.lastIndex = hStart + hLen;
            continue;
          }
          tokenRe.lastIndex = mStart + dirLen;
          continue;
        }
        if (langId === "python" || langId === "bazel" || langId === "config" || langId === "generic") {
          tokCls = "tok-comment";
        }
      } else if (c0 === 64) {
        tokCls = scheme === "semantic_review" ? "" : "tok-macro";
      } else if (c0 === 60 && tokStr.charCodeAt(1) === 33) {
        tokCls = "tok-comment";
      } else {
        tokCls = scheme === "semantic_review" ? "" : "tok-op";
      }
      if (tokCls) {
        spans.push({ start: mStart, end: mEnd, cls: tokCls });
      }
    }
    return spans;
  }

  let _lastHlLang = "";
  let _lastHlScheme = "";
  let _lastHlShowTabs = false;
  let _lastHlShowWs = false;
  let _lastHlSyn = false;
  let _lastHlPrefix = "";
  let _lastHlIsCanon = false;

  function appendDecoratedGapSlice(rawSlice, lineNeedsDecorate, hasTab, hasBugRef) {
    if (!lineNeedsDecorate || !rawSlice) return rawSlice;
    if (!hasTab && !hasBugRef) {
      return _HTML_ESCAPE_TEST_RE.test(rawSlice)
        ? rawSlice.replace(_HTML_ESCAPE_REPLACE_RE, _escapeHtmlChar)
        : rawSlice;
    }
    return decorateDiffLineSlice(rawSlice, false, hasTab, hasBugRef);
  }

  function appendDecoratedTokenSlice(tokStr, tokCls, lineNeedsDecorate, hasTab, hasBugRef) {
    if (!lineNeedsDecorate) return tokStr;
    const mayNeedEscape = (
      tokCls === "tok-comment" ||
      tokCls === "tok-string" ||
      tokCls === "tok-op" ||
      tokCls === "tok-macro"
    );
    if (!mayNeedEscape) return tokStr;
    if (!hasTab && !hasBugRef) {
      return _HTML_ESCAPE_TEST_RE.test(tokStr)
        ? tokStr.replace(_HTML_ESCAPE_REPLACE_RE, _escapeHtmlChar)
        : tokStr;
    }
    return decorateDiffLineSlice(tokStr, false, hasTab, hasBugRef);
  }

  function isLeadingWhitespaceOnly(text, startIdx, endIdx) {
    for (let i = startIdx; i < endIdx; i++) {
      if (text.charCodeAt(i) > 32) return false;
    }
    return true;
  }

  function renderFastSyntaxLineHtml(text, langId, scheme, lineNeedsDecorate, hasTab, hasBugRef, hasNonQuoteEscapeOpt, firstQuoteIdxOpt) {
    const len = text.length;
    let offset = 0;
    let html = "";
    const ch0 = text.charCodeAt(0);
    if (ch0 === 43 || ch0 === 45 || ch0 === 32) {
      html = `<span class="tok-prefix">${text[0]}</span>`;
      offset = 1;
    }
    if (offset >= len) return html;

    if (scheme === "intraline_focus") {
      const isHashComment = langId === "python" || langId === "bazel" || langId === "config";
      const commentIdx = isHashComment ? text.indexOf("#", offset) : text.indexOf("//", offset);
      if (commentIdx === offset || (commentIdx > offset && text.charCodeAt(commentIdx - 1) <= 32)) {
        if (commentIdx > offset) {
          html += appendDecoratedGapSlice(text.slice(offset, commentIdx), lineNeedsDecorate, hasTab, hasBugRef);
        }
        const commentEsc = appendDecoratedGapSlice(text.slice(commentIdx), lineNeedsDecorate, hasTab, hasBugRef);
        html += `<span class="tok-comment">${commentEsc}</span>`;
        return html;
      }
      html += appendDecoratedGapSlice(offset > 0 ? text.slice(offset) : text, lineNeedsDecorate, hasTab, hasBugRef);
      return html;
    }

    const tokenRe = _DIFF_CODE_LINE_FAST_RE;
    tokenRe.lastIndex = offset;
    let prevEnd = offset;
    const simpleEscapeOnly = lineNeedsDecorate && !hasTab && !hasBugRef;
    const hasAmpLtGtSQuote = simpleEscapeOnly && (
      hasNonQuoteEscapeOpt !== undefined
        ? hasNonQuoteEscapeOpt
        : _HTML_NON_QUOTE_ESCAPE_RE.test(text)
    );
    const firstQuoteIdx = simpleEscapeOnly
      ? (firstQuoteIdxOpt !== undefined ? firstQuoteIdxOpt : text.indexOf('"'))
      : -1;
    const lastQuoteIdx = firstQuoteIdx !== -1 ? text.lastIndexOf('"') : -1;
    let match;
    while ((match = tokenRe.exec(text)) !== null) {
      const mStart = match.index;
      const tokStr = match[0];
      const mEnd = mStart + tokStr.length;
      const c0 = tokStr.charCodeAt(0);
      let tokCls = "";
      let mayEscapeTok = false;
      if ((c0 >= 65 && c0 <= 90) || (c0 >= 97 && c0 <= 122) || c0 === 95 || c0 >= 128) {
        tokCls = classifyIdentifierToken(tokStr, null, scheme, langId, text, mEnd);
      } else if (c0 >= 48 && c0 <= 57) {
        tokCls = scheme === "semantic_review" ? "" : "tok-number";
      } else if (c0 === 34 || c0 === 39 || c0 === 96) {
        tokCls = scheme === "semantic_review" ? "" : "tok-string";
        mayEscapeTok = true;
      } else if (c0 === 47) {
        const c1 = tokStr.charCodeAt(1);
        if (c1 === 47 || c1 === 42) {
          tokCls = "tok-comment";
          mayEscapeTok = true;
        } else {
          tokCls = scheme === "semantic_review" ? "" : "tok-op";
        }
      } else if (c0 === 35) {
        if (langId === "cpp" && _CPP_PREPROC_DIR_RE.test(tokStr) && isLeadingWhitespaceOnly(text, offset, mStart)) {
          if (mStart > prevEnd) {
            html += appendDecoratedGapSlice(text.slice(prevEnd, mStart), lineNeedsDecorate, hasTab, hasBugRef);
          }
          const dirMatch = tokStr.match(_CPP_PREPROC_HEAD_RE);
          const dirStr = dirMatch ? dirMatch[0] : "#";
          const dirLen = dirStr.length;
          const dirEsc = appendDecoratedTokenSlice(dirStr, "tok-macro", lineNeedsDecorate, hasTab, hasBugRef);
          html += `<span class="tok-macro">${dirEsc}</span>`;
          const restOfDirective = tokStr.slice(dirLen);
          const headerAngleMatch = restOfDirective.match(_CPP_INCLUDE_ANGLE_RE);
          if (dirStr.indexOf("include") !== -1 && headerAngleMatch) {
            const wsStr = headerAngleMatch[1];
            const hStr = headerAngleMatch[2];
            const hEnd = mStart + dirLen + wsStr.length + hStr.length;
            if (wsStr.length > 0) {
              html += appendDecoratedGapSlice(wsStr, lineNeedsDecorate, hasTab, hasBugRef);
            }
            const hEsc = appendDecoratedTokenSlice(hStr, "tok-string", lineNeedsDecorate, hasTab, hasBugRef);
            html += `<span class="tok-string">${hEsc}</span>`;
            prevEnd = hEnd;
            tokenRe.lastIndex = hEnd;
            continue;
          }
          prevEnd = mStart + dirLen;
          tokenRe.lastIndex = prevEnd;
          continue;
        }
        if (langId === "python" || langId === "bazel" || langId === "config" || langId === "generic") {
          tokCls = "tok-comment";
          mayEscapeTok = true;
        }
      } else if (c0 === 64) {
        tokCls = scheme === "semantic_review" ? "" : "tok-macro";
      } else if (c0 === 60 && tokStr.charCodeAt(1) === 33) {
        tokCls = "tok-comment";
        mayEscapeTok = true;
      } else {
        tokCls = scheme === "semantic_review" ? "" : "tok-op";
        mayEscapeTok = (c0 === 60 || c0 === 62 || c0 === 38 || c0 === 45);
      }
      if (tokCls) {
        if (mStart > prevEnd) {
          if (mStart === prevEnd + 1 && text.charCodeAt(prevEnd) === 32) {
            html += " ";
          } else {
            const gapSlice = text.slice(prevEnd, mStart);
            if (!lineNeedsDecorate) {
              html += gapSlice;
            } else if (simpleEscapeOnly) {
              if (!hasAmpLtGtSQuote && (firstQuoteIdx === -1 || mStart <= firstQuoteIdx || prevEnd > lastQuoteIdx || gapSlice.indexOf('"') === -1)) {
                html += gapSlice;
              } else {
                html += _HTML_ESCAPE_TEST_RE.test(gapSlice)
                  ? gapSlice.replace(_HTML_ESCAPE_REPLACE_RE, _escapeHtmlChar)
                  : gapSlice;
              }
            } else {
              html += decorateDiffLineSlice(gapSlice, false, hasTab, hasBugRef);
            }
          }
        }
        let tokEsc = tokStr;
        if (lineNeedsDecorate && mayEscapeTok) {
          if (simpleEscapeOnly) {
            if (c0 === 34 && !hasAmpLtGtSQuote && ((mStart === firstQuoteIdx && mEnd - 1 === lastQuoteIdx) || tokStr.indexOf('"', 1) === tokStr.length - 1)) {
              tokEsc = `&quot;${tokStr.slice(1, -1)}&quot;`;
            } else if (!hasAmpLtGtSQuote && (firstQuoteIdx === -1 || mEnd <= firstQuoteIdx || mStart > lastQuoteIdx || tokStr.indexOf('"') === -1)) {
              tokEsc = tokStr;
            } else if (_HTML_ESCAPE_TEST_RE.test(tokStr)) {
              tokEsc = tokStr.replace(_HTML_ESCAPE_REPLACE_RE, _escapeHtmlChar);
            }
          } else {
            tokEsc = decorateDiffLineSlice(tokStr, false, hasTab, hasBugRef);
          }
        }
        html += `<span class="${tokCls}">${tokEsc}</span>`;
        prevEnd = mEnd;
      }
    }
    if (prevEnd < len) {
      const tailSlice = prevEnd > 0 ? text.slice(prevEnd) : text;
      if (!lineNeedsDecorate) {
        html += tailSlice;
      } else if (simpleEscapeOnly) {
        if (!hasAmpLtGtSQuote && (firstQuoteIdx === -1 || prevEnd > lastQuoteIdx || tailSlice.indexOf('"') === -1)) {
          html += tailSlice;
        } else {
          html += _HTML_ESCAPE_TEST_RE.test(tailSlice)
            ? tailSlice.replace(_HTML_ESCAPE_REPLACE_RE, _escapeHtmlChar)
            : tailSlice;
        }
      } else {
        html += decorateDiffLineSlice(tailSlice, false, hasTab, hasBugRef);
      }
    }
    return html;
  }

  function renderAndCacheSyntaxLineHtml(text, langId, scheme, showTabs, showWsErrors, lineCacheKey) {
    if (!text) return "";
    if (showWsErrors) {
      const lastCode = text.charCodeAt(text.length - 1);
      if (lastCode === 32 || lastCode === 9) {
        return renderHighlightedDiffLineHtml(text, "ctx", langId, scheme, null);
      }
    }
    const hasTab = showTabs && text.indexOf("\t") !== -1;
    const hasBugRef = text.indexOf("b/") !== -1 || text.indexOf("B/") !== -1;
    const hasNonQuoteEscape = _HTML_NON_QUOTE_ESCAPE_RE.test(text);
    const firstQuoteIdx = text.indexOf('"');
    const lineNeedsDecorate = hasTab || hasBugRef || hasNonQuoteEscape || firstQuoteIdx !== -1;
    const fastHtml = renderFastSyntaxLineHtml(text, langId, scheme, lineNeedsDecorate, hasTab, hasBugRef, hasNonQuoteEscape, firstQuoteIdx);
    if (lineCacheKey) {
      if (_pgHighlightedLineHtmlCache.size >= PG_HIGHLIGHTED_LINE_CACHE_MAX) {
        _pgHighlightedLineHtmlCache.clear();
      }
      _pgHighlightedLineHtmlCache.set(lineCacheKey, fastHtml);
    }
    return fastHtml;
  }

  function renderHighlightedDiffLineHtml(rawLine, lineType, langId, scheme, intralineSpans) {
    const text = String(rawLine || "");
    if (!text) return "";
    const prefs = state.diffPreferences || state.pgDiffPreferences || getPgDiffPreferences();
    const showTabs = Boolean(prefs && prefs.showTabs);
    const showWsErrors = Boolean(prefs && (prefs.showWhitespaceErrors !== undefined ? prefs.showWhitespaceErrors : true));
    const syntaxOn = Boolean(!prefs || prefs.syntaxHighlighting !== false);
    const intra = Array.isArray(intralineSpans) && intralineSpans.length > 0 ? intralineSpans : null;
    const intraClass = lineType === "add" ? "diff-intra-add" : (lineType === "del" ? "diff-intra-del" : "");
    const hasIntraline = Boolean(intraClass && intra);

    let lineCacheKey = "";
    if (text.length <= 512) {
      if (
        langId !== _lastHlLang ||
        scheme !== _lastHlScheme ||
        showTabs !== _lastHlShowTabs ||
        showWsErrors !== _lastHlShowWs ||
        syntaxOn !== _lastHlSyn
      ) {
        _lastHlLang = langId;
        _lastHlScheme = scheme;
        _lastHlShowTabs = showTabs;
        _lastHlShowWs = showWsErrors;
        _lastHlSyn = syntaxOn;
        const hlSchemeKey = (scheme === "intraline_focus" || scheme === "semantic_review" || scheme === "classic")
          ? scheme
          : "syntax_intraline";
        _lastHlPrefix = `${langId}\x00${hlSchemeKey}\x00${showTabs ? 1 : 0}${showWsErrors ? 1 : 0}${syntaxOn ? 1 : 0}\x00`;
        _lastHlIsCanon = _lastHlPrefix === _CANONICAL_CPP_HL_PREFIX;
      }
      if (!hasIntraline) {
        lineCacheKey = _lastHlIsCanon ? text : _lastHlPrefix + text;
      } else if (intra.length === 1) {
        lineCacheKey = `${_lastHlPrefix}${intraClass}:${intra[0][0]},${intra[0][1]}\x00${text}`;
      } else {
        let intraKey = intraClass;
        for (let i = 0; i < intra.length; i++) {
          intraKey += `:${intra[i][0]},${intra[i][1]}`;
        }
        lineCacheKey = `${_lastHlPrefix}${intraKey}\x00${text}`;
      }
      const cachedHtml = _pgHighlightedLineHtmlCache.get(lineCacheKey);
      if (cachedHtml !== undefined) {
        return cachedHtml;
      }
    }

    let trailingStart = -1;
    if (showWsErrors) {
      const lastCode = text.charCodeAt(text.length - 1);
      if (lastCode === 32 || lastCode === 9) {
        let k = text.length - 1;
        while (k > 0 && (text.charCodeAt(k - 1) === 32 || text.charCodeAt(k - 1) === 9)) {
          k--;
        }
        trailingStart = k;
      }
    }

    const hasTab = showTabs && text.indexOf("\t") !== -1;
    const hasBugRef = text.indexOf("b/") !== -1 || text.indexOf("B/") !== -1;
    const hasNonQuoteEscape = _HTML_NON_QUOTE_ESCAPE_RE.test(text);
    const firstQuoteIdx = text.indexOf('"');
    const lineNeedsDecorate = hasTab || hasBugRef || hasNonQuoteEscape || firstQuoteIdx !== -1;

    if (scheme === "classic" || !syntaxOn) {
      let plainResult;
      if (trailingStart >= 0) {
        const bodyPart = lineNeedsDecorate
          ? decorateDiffLineSlice(text.slice(0, trailingStart), false, hasTab, hasBugRef)
          : text.slice(0, trailingStart);
        const trailPart = decorateDiffLineSlice(text.slice(trailingStart), true, hasTab, false);
        plainResult = bodyPart + trailPart;
      } else {
        plainResult = lineNeedsDecorate
          ? decorateDiffLineSlice(text, false, hasTab, hasBugRef)
          : text;
      }
      if (lineCacheKey) {
        if (_pgHighlightedLineHtmlCache.size >= PG_HIGHLIGHTED_LINE_CACHE_MAX) {
          _pgHighlightedLineHtmlCache.clear();
        }
        _pgHighlightedLineHtmlCache.set(lineCacheKey, plainResult);
      }
      return plainResult;
    }

    // Fused single-pass fast path for lines without intraline ranges or trailing whitespace errors (>90% of diff lines)
    if (!hasIntraline && trailingStart < 0) {
      const fastHtml = renderFastSyntaxLineHtml(text, langId, scheme, lineNeedsDecorate, hasTab, hasBugRef, hasNonQuoteEscape, firstQuoteIdx);
      if (lineCacheKey) {
        if (_pgHighlightedLineHtmlCache.size >= PG_HIGHLIGHTED_LINE_CACHE_MAX) {
          _pgHighlightedLineHtmlCache.clear();
        }
        _pgHighlightedLineHtmlCache.set(lineCacheKey, fastHtml);
      }
      return fastHtml;
    }

    const syntaxSpans = tokenizeDiffCodeLine(text, langId, scheme);

    let html = "";
    let pos = 0;
    const len = text.length;
    let synIdx = 0;
    let intraIdx = 0;
    const synLen = syntaxSpans.length;
    const intraLen = hasIntraline ? intra.length : 0;
    while (pos < len) {
      while (synIdx < synLen && syntaxSpans[synIdx].end <= pos) synIdx++;
      while (intraIdx < intraLen && Math.min(len, intra[intraIdx][1]) <= pos) intraIdx++;
      let nextPos = len;
      if (trailingStart > pos && trailingStart < nextPos) {
        nextPos = trailingStart;
      }
      if (synIdx < synLen) {
        const sp = syntaxSpans[synIdx];
        const bound = sp.start > pos ? sp.start : sp.end;
        if (bound > pos && bound < nextPos) nextPos = bound;
      }
      if (intraIdx < intraLen) {
        const iv = intra[intraIdx];
        const ivStart = iv[0] < 0 ? 0 : (iv[0] > len ? len : iv[0]);
        const ivEnd = iv[1] < 0 ? 0 : (iv[1] > len ? len : iv[1]);
        const bound = ivStart > pos ? ivStart : ivEnd;
        if (bound > pos && bound < nextPos) nextPos = bound;
      }
      if (nextPos <= pos) {
        nextPos = pos + 1;
      }
      const synCls = (synIdx < synLen && syntaxSpans[synIdx].start <= pos && syntaxSpans[synIdx].end > pos)
        ? syntaxSpans[synIdx].cls
        : "";
      const hasIntra = Boolean(
        intraIdx < intraLen &&
        Math.max(0, intra[intraIdx][0]) <= pos &&
        Math.min(len, intra[intraIdx][1]) > pos
      );
      const isTrailing = trailingStart >= 0 && pos >= trailingStart;
      const wsErrorCls = isTrailing ? "pg-ws-error pg-trailing-ws" : "";
      const combinedCls = (synCls ? synCls : "") +
        (hasIntra ? (synCls ? " " + intraClass : intraClass) : "") +
        (wsErrorCls ? ((synCls || hasIntra) ? " " + wsErrorCls : wsErrorCls) : "");
      const rawSeg = text.slice(pos, nextPos);
      const sliceEscaped = lineNeedsDecorate
        ? decorateDiffLineSlice(rawSeg, false, hasTab, !isTrailing && hasBugRef)
        : rawSeg;
      html += combinedCls ? `<span class="${combinedCls}">${sliceEscaped}</span>` : sliceEscaped;
      pos = nextPos;
    }
    if (lineCacheKey) {
      if (_pgHighlightedLineHtmlCache.size >= PG_HIGHLIGHTED_LINE_CACHE_MAX) {
        _pgHighlightedLineHtmlCache.clear();
      }
      _pgHighlightedLineHtmlCache.set(lineCacheKey, html);
    }
    return html;
  }

  function renderHunkHeaderInnerHtml(rawText, scheme) {
    const text = String(rawText || "");
    if (scheme === "classic") return escapeHtml(text);
    const m = text.match(/^(@@\s*-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s*@@)(.*)$/);
    if (!m) return escapeHtml(text);
    const rangePart = escapeHtml(m[1]);
    const ctxPart = escapeHtml(m[2] || "");
    return `<span class="hunk-range-pill">${rangePart}</span><span class="hunk-context-sig">${ctxPart}</span>`;
  }

  const PG_REVIEWED_STORAGE_PREFIX = "pg_reviewed_";

  function safeEscapeCss(val) {
    if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
      return CSS.escape(String(val));
    }
    return String(val).replace(/["\\]/g, "\\$&");
  }

  function getPgReviewedStorageKey(clNum, patchSet) {
    return `${PG_REVIEWED_STORAGE_PREFIX}${Number(clNum)}_ps${Number(patchSet || 1)}`;
  }

  let _lastReviewedSetMapRef = null;
  let _lastReviewedSetCl = -1;
  let _lastReviewedSetPs = -1;
  let _lastReviewedSetVal = null;

  function getPgReviewedSet(clNum, patchSet) {
    const num = Number(clNum);
    const ps = Number(patchSet || 1);
    if (
      _lastReviewedSetMapRef === state.reviewedFilesByClPs &&
      _lastReviewedSetCl === num &&
      _lastReviewedSetPs === ps &&
      _lastReviewedSetVal !== null
    ) {
      return _lastReviewedSetVal;
    }
    if (!state.reviewedFilesByClPs) state.reviewedFilesByClPs = {};
    const key = getPgReviewedStorageKey(num, ps);
    const existing = state.reviewedFilesByClPs[key];
    if (existing && typeof existing.has === "function") {
      _lastReviewedSetMapRef = state.reviewedFilesByClPs;
      _lastReviewedSetCl = num;
      _lastReviewedSetPs = ps;
      _lastReviewedSetVal = existing;
      return _lastReviewedSetVal;
    }
    const set = new Set();
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        const raw = window.localStorage.getItem(key);
        if (raw) {
          const arr = JSON.parse(raw);
          if (Array.isArray(arr)) {
            arr.forEach(f => set.add(String(f)));
          }
        }
      }
    } catch (_e) {}
    state.reviewedFilesByClPs[key] = set;
    _lastReviewedSetMapRef = state.reviewedFilesByClPs;
    _lastReviewedSetCl = num;
    _lastReviewedSetPs = ps;
    _lastReviewedSetVal = set;
    return set;
  }

  function savePgReviewedSet(clNum, patchSet, set) {
    const num = Number(clNum);
    const ps = Number(patchSet || 1);
    const key = getPgReviewedStorageKey(num, ps);
    if (!state.reviewedFilesByClPs) state.reviewedFilesByClPs = {};
    state.reviewedFilesByClPs[key] = set;
    _lastReviewedSetMapRef = state.reviewedFilesByClPs;
    _lastReviewedSetCl = num;
    _lastReviewedSetPs = ps;
    _lastReviewedSetVal = set;
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        window.localStorage.setItem(key, JSON.stringify(Array.from(set)));
      }
    } catch (_e) {}
  }

  function isPgFileReviewed(clNum, patchSet, filePath) {
    const num = +clNum;
    const ps = +(patchSet || 1);
    const set = (
      _lastReviewedSetMapRef === state.reviewedFilesByClPs &&
      _lastReviewedSetCl === num &&
      _lastReviewedSetPs === ps &&
      _lastReviewedSetVal !== null
    ) ? _lastReviewedSetVal : getPgReviewedSet(num, ps);
    return set.size > 0 && set.has(typeof filePath === "string" ? filePath : String(filePath || ""));
  }

  let _lastCollapsedSetMapRef = null;
  let _lastCollapsedSetCl = -1;
  let _lastCollapsedSetPs = -1;
  let _lastCollapsedSetVal = null;

  function getPgCollapsedSet(clNum, patchSet) {
    const num = +clNum;
    const ps = +(patchSet || 1);
    if (
      _lastCollapsedSetMapRef === state.collapsedFilesByClPs &&
      _lastCollapsedSetCl === num &&
      _lastCollapsedSetPs === ps &&
      _lastCollapsedSetVal !== null
    ) {
      return _lastCollapsedSetVal;
    }
    if (!state.collapsedFilesByClPs) state.collapsedFilesByClPs = {};
    const key = `${num}_ps${ps}`;
    const existing = state.collapsedFilesByClPs[key];
    if (!existing || typeof existing.has !== "function") {
      const revSet = getPgReviewedSet(num, ps);
      state.collapsedFilesByClPs[key] = new Set(revSet);
    }
    _lastCollapsedSetMapRef = state.collapsedFilesByClPs;
    _lastCollapsedSetCl = num;
    _lastCollapsedSetPs = ps;
    _lastCollapsedSetVal = state.collapsedFilesByClPs[key];
    return _lastCollapsedSetVal;
  }

  function isPgFileCollapsed(clNum, patchSet, filePath) {
    const pathStr = typeof filePath === "string" ? filePath : String(filePath || "");
    if (pathStr.charCodeAt(0) === 47) {
      if (pathStr === "/COMMIT_MSG") {
        return Boolean(state.collapsedCommitMsgs && state.collapsedCommitMsgs.size > 0 && state.collapsedCommitMsgs.has(+clNum));
      }
      if (pathStr === "/MERGE_LIST") {
        return Boolean(state.collapsedMergeLists && state.collapsedMergeLists.size > 0 && state.collapsedMergeLists.has(+clNum));
      }
    }
    const num = +clNum;
    const ps = +(patchSet || 1);
    const set = (
      _lastCollapsedSetMapRef === state.collapsedFilesByClPs &&
      _lastCollapsedSetCl === num &&
      _lastCollapsedSetPs === ps &&
      _lastCollapsedSetVal !== null
    ) ? _lastCollapsedSetVal : getPgCollapsedSet(num, ps);
    return set.size > 0 && set.has(pathStr);
  }

  function setPgFileCollapsed(clNum, patchSet, filePath, shouldCollapse) {
    const ps = Number(patchSet || 1);
    const collapsedSet = getPgCollapsedSet(clNum, ps);
    const pathStr = String(filePath || "");
    if (shouldCollapse) {
      collapsedSet.add(pathStr);
    } else {
      collapsedSet.delete(pathStr);
    }

    if (pathStr === "/COMMIT_MSG") {
      if (!state.collapsedCommitMsgs) state.collapsedCommitMsgs = new Set();
      if (shouldCollapse) {
        state.collapsedCommitMsgs.add(Number(clNum));
      } else {
        state.collapsedCommitMsgs.delete(Number(clNum));
      }
      const panel = document.getElementById(`commitMsgPanel-${clNum}`);
      const btn = document.getElementById(`commitMsgToggleBtn-${clNum}`);
      if (panel && panel.classList) {
        panel.classList.toggle("collapsed", shouldCollapse);
        panel.classList.toggle("is-collapsed", shouldCollapse);
      }
      if (btn) btn.textContent = shouldCollapse ? "Show ▾" : "Hide ▴";
      const chevron = (panel && typeof panel.querySelector === "function")
        ? panel.querySelector(".pg-commit-msg-toggle-btn .pg-file-chevron")
        : null;
      if (chevron) chevron.textContent = shouldCollapse ? "▶" : "▼";
    } else if (pathStr === "/MERGE_LIST") {
      if (!state.collapsedMergeLists) state.collapsedMergeLists = new Set();
      if (shouldCollapse) {
        state.collapsedMergeLists.add(Number(clNum));
      } else {
        state.collapsedMergeLists.delete(Number(clNum));
      }
      const panel = document.getElementById(`mergeListPanel-${clNum}`);
      const btn = document.getElementById(`mergeListToggleBtn-${clNum}`);
      if (panel && panel.classList) {
        panel.classList.toggle("collapsed", shouldCollapse);
        panel.classList.toggle("is-collapsed", shouldCollapse);
      }
      if (btn) btn.textContent = shouldCollapse ? "Show ▾" : "Hide ▴";
      const chevron = (panel && typeof panel.querySelector === "function")
        ? panel.querySelector(".pg-merge-list-toggle-btn .pg-file-chevron")
        : null;
      if (chevron) chevron.textContent = shouldCollapse ? "▶" : "▼";
    }

    const escaped = safeEscapeCss(pathStr);
    const blockEls = document.querySelectorAll(`.pg-file-diff-block[data-cl="${clNum}"][data-file="${escaped}"]`);
    blockEls.forEach(block => {
      if (shouldCollapse) {
        block.classList.add("is-collapsed");
      } else {
        block.classList.remove("is-collapsed");
      }
      const chevron = block.querySelector(".pg-file-chevron");
      if (chevron) chevron.textContent = shouldCollapse ? "▶" : "▼";
      const codeTable = block.querySelector(".code-table");
      if (codeTable) codeTable.style.display = shouldCollapse ? "none" : "";
      const imageViewer = block.querySelector(".pg-image-viewer");
      if (imageViewer) {
        imageViewer.style.display = shouldCollapse ? "none" : "";
        if (!shouldCollapse && typeof loadPgImageData === "function") {
          const fileId = block.id.replace(`file-diff-${clNum}-`, "");
          const status = imageViewer.getAttribute("data-status") || "M";
          loadPgImageData(clNum, ps, pathStr, fileId, status);
        }
      }
      const binaryCard = block.querySelector(".pg-binary-file-card");
      if (binaryCard) binaryCard.style.display = shouldCollapse ? "none" : "";
    });

    const rowEls = document.querySelectorAll(`.pg-file-row[data-cl="${clNum}"][data-file="${escaped}"]`);
    rowEls.forEach(row => {
      row.classList.toggle("is-collapsed", shouldCollapse);
      const rowChevron = row.querySelector(".pg-magic-row-toggle-btn .pg-file-chevron");
      if (rowChevron) rowChevron.textContent = shouldCollapse ? "▶" : "▼";
      const rowToggleBtn = row.querySelector(".pg-magic-row-toggle-btn");
      if (rowToggleBtn) {
        rowToggleBtn.title = shouldCollapse ? `Expand ${pathStr} (i)` : `Collapse ${pathStr} (i)`;
      }
    });
    if (!shouldCollapse) {
      const prefs = getPgDiffPreferences();
      if (prefs && prefs.autoMarkViewed) {
        if (typeof isPgFileReviewed === "function" && typeof setPgFileReviewed === "function" && !isPgFileReviewed(clNum, ps, pathStr)) {
          setPgFileReviewed(clNum, ps, pathStr, true);
        }
      }
    }
    syncPgExpandAllButtonState(clNum, ps);
  }

