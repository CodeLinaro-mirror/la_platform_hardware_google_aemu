  async function resolveSidecarProjectId() {
    if (state.cachedSidecarProjectId !== null) {
      return state.cachedSidecarProjectId || "";
    }
    const parentConv = (window.sidecar && window.sidecar.conversationId) || "";
    if (!parentConv || !window.sidecar || !window.sidecar.agent || !window.sidecar.agent.getConversationMetadata) {
      state.cachedSidecarProjectId = "";
      return "";
    }
    try {
      const meta = await window.sidecar.agent.getConversationMetadata(parentConv);
      const pid = String((meta && (meta.projectId || meta.project_id || (meta.metadata && meta.metadata.projectId))) || "").trim();
      state.cachedSidecarProjectId = pid;
      return pid;
    } catch (_err) {
      state.cachedSidecarProjectId = "";
      return "";
    }
  }

  async function dispatchIsolatedCodeQuestion(sel, questionItem, formattedPrompt, question) {
    const projectId = await resolveSidecarProjectId();
    const resp = await sidecarFetch("/api/gerrit/ask-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cl: sel.clNum,
        file_path: sel.filePath,
        start_line: sel.startLine,
        end_line: sel.endLine,
        side: sel.side,
        code_snippet: sel.codeSnippet,
        question,
        spawnNew: true,
        threadId: questionItem.id,
        function_name: questionItem.function_name || "",
        projectId,
        viewer: state.viewer || "",
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error((data && data.error) || `HTTP ${resp.status}`);
    }
    const dispatched = Boolean(data && data.dispatched);
    if (!dispatched) {
      navigator.clipboard?.writeText(formattedPrompt);
    }
    return {
      dispatched,
      conversationId: String((data && data.conversation_id) || ""),
      turns: Array.isArray(data && data.turns) ? data.turns : null,
    };
  }

  async function submitSelectedCodeQuestion(_btnEl) {
    const sel = state.codeSelection;
    if (!sel) return;
    const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
    const question = (inputEl ? inputEl.value : (sel.draft || sel.askDraft || sel.commentDraft || "")).trim()
      || "Please explain this highlighted code block and check for any potential bugs or edge cases.";
    sel.draft = question;
    sel.commentDraft = question;
    sel.askDraft = question;

    const rangeLabel = sel.startLine === sel.endLine ? `L${sel.startLine}` : `L${sel.startLine}-${sel.endLine}`;
    const sideNote = sel.side === "PARENT" ? " (PARENT / base)" : "";
    const formattedPrompt =
      `Regarding Gerrit CL \`ag/${sel.clNum}\` in \`${sel.filePath}\` (${rangeLabel}${sideNote}):\n\n` +
      `\`\`\`\n${sel.codeSnippet}\n\`\`\`\n\n` +
      `**Question:** ${question}`;
    const functionName = sel.altId
      ? String(sel.altTitle || sel.altId)
      : resolveEnclosingSymbolForDiffLine(
          sel.clNum,
          sel.filePath,
          sel.startIdx,
          sel.startLine,
          sel.side
        );

    if (!state.inlineCommentsByCl[sel.clNum]) state.inlineCommentsByCl[sel.clNum] = [];
    const viewerLdap = (state.viewer || "you").split("@")[0];
    const questionItem = {
      id: `inline-${state.nextInlineItemId++}`,
      conversation_id: "",
      kind: "agent_question",
      author: viewerLdap,
      author_name: viewerLdap,
      participants: [viewerLdap],
      file_path: sel.filePath,
      line: sel.endLine,
      start_line: sel.startLine,
      end_line: sel.endLine,
      start_idx: sel.startIdx,
      end_idx: sel.endIdx,
      side: sel.side,
      alt_id: sel.altId || "",
      alt_title: sel.altTitle || "",
      function_name: functionName,
      initial_question: question,
      message: question,
      reply: "",
      reply_status: "thinking",
      turns: [{ turn_index: 0, question, reply: "", status: "thinking", author: viewerLdap }],
      prompt: formattedPrompt,
      code_snippet: sel.codeSnippet,
      sending: true,
      dispatched: false,
      collapsed: false,
    };
    sel.savedInlineId = questionItem.id;
    sel.sentPrompt = formattedPrompt;
    state.inlineCommentsByCl[sel.clNum].push(questionItem);

    clearCodeSelection();
    insertOrReplaceInlineItemInDom(sel.clNum, questionItem, { forceRender: true });

    try {
      const res = await dispatchIsolatedCodeQuestion(sel, questionItem, formattedPrompt, question);
      questionItem.sending = false;
      questionItem.dispatched = res.dispatched;
      if (res.conversationId) questionItem.conversation_id = res.conversationId;
      if (res.turns) questionItem.turns = res.turns;
      insertOrReplaceInlineItemInDom(sel.clNum, questionItem, { forceRender: true });
      ensureCodeQuestionPollingTimer();
      const locLabel = formatFunctionLineLabel(functionName, sel.filePath, sel.startLine);
      document.getElementById("drawerStatus").textContent =
        `Started isolated agent Q&A at ${locLabel} on ag/${sel.clNum}`;
      showToast(
        res.dispatched
          ? `🤖 Started background agent Q&A at ${locLabel}!`
          : `🤖 Prepared code question (${locLabel}) and copied to clipboard!`
      );
    } catch (err) {
      questionItem.sending = false;
      insertOrReplaceInlineItemInDom(sel.clNum, questionItem, { forceRender: true });
      showToast("Could not send question to agent: " + err);
    }
  }

  function onQaInlineTextareaKeydown(event, clNum, itemId, mode) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (mode === "modify") saveModifiedInflightQuestion(clNum, itemId);
      else submitInlineFollowupQuestion(clNum, itemId);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      if (mode === "modify") cancelModifyInflightQuestion(clNum, itemId);
      else cancelInlineFollowupComposer(clNum, itemId);
    }
  }

  function startModifyInflightQuestion(clNum, itemId) {
    const item = findInlineItemById(clNum, itemId);
    if (!item || !isQuestionThreadPending(item)) return;
    const turns = normalizeQuestionTurns(item);
    const latest = turns[turns.length - 1];
    state.editingQaItemById[item.id] = latest ? latest.question : (item.message || "");
    insertOrReplaceInlineItemInDom(clNum, item, {
      forceRender: true,
      focusTextareaId: `qaModifyInput-${item.id}`,
    });
  }

  function cancelModifyInflightQuestion(clNum, itemId) {
    const item = findInlineItemById(clNum, itemId);
    delete state.editingQaItemById[itemId];
    if (item) {
      insertOrReplaceInlineItemInDom(clNum, item, { forceRender: true });
    }
  }

  async function saveModifiedInflightQuestion(clNum, itemId) {
    const num = Number(clNum);
    const item = findInlineItemById(num, itemId);
    if (!item || state.qaActionInFlightById.has(item.id)) return;
    const ta = document.getElementById(`qaModifyInput-${item.id}`);
    const newQuestion = String((ta ? ta.value : state.editingQaItemById[item.id]) || "").trim();
    if (!newQuestion) {
      showToast("Enter a non-empty question or click Delete to cancel it.");
      return;
    }

    state.qaActionInFlightById.add(item.id);
    insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
    try {
      const projectId = await resolveSidecarProjectId();
      const resp = await sidecarFetch("/api/gerrit/code-question-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "modify",
          cl: num,
          conversationId: item.conversation_id || "",
          threadId: item.id,
          question: newQuestion,
          file_path: item.file_path || "",
          start_line: item.start_line || item.line || 1,
          end_line: item.end_line || item.line || 1,
          side: item.side || "REVISION",
          code_snippet: item.code_snippet || "",
          function_name: item.function_name || "",
          projectId,
          viewer: state.viewer || "",
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error((data && data.error) || `HTTP ${resp.status}`);

      delete state.editingQaItemById[item.id];
      state.qaActionInFlightById.delete(item.id);
      if (data.conversation_id) item.conversation_id = data.conversation_id;
      if (Array.isArray(data.turns) && data.turns.length > 0) {
        item.turns = data.turns;
        item.initial_question = data.turns[0].question;
      } else {
        const turns = normalizeQuestionTurns(item);
        turns[turns.length - 1] = { turn_index: turns.length - 1, question: newQuestion, reply: "", status: "thinking" };
        item.turns = turns;
      }
      item.message = newQuestion;
      item.reply = "";
      item.reply_status = "thinking";
      item.sending = false;
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
      ensureCodeQuestionPollingTimer();
      showToast("✎ Updated and resent question to Jetski Agent!");
    } catch (err) {
      state.qaActionInFlightById.delete(item.id);
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
      showToast("Could not modify question: " + err);
    }
  }

  async function deleteInflightCodeQuestion(clNum, itemId) {
    const num = Number(clNum);
    const item = findInlineItemById(num, itemId);
    if (!item || state.qaActionInFlightById.has(item.id)) return;

    state.qaActionInFlightById.add(item.id);
    insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
    try {
      const resp = await sidecarFetch("/api/gerrit/code-question-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "delete",
          cl: num,
          conversationId: item.conversation_id || "",
          threadId: item.id,
          viewer: state.viewer || "",
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error((data && data.error) || `HTTP ${resp.status}`);

      state.qaActionInFlightById.delete(item.id);
      delete state.editingQaItemById[item.id];
      if (data.thread_deleted || !Array.isArray(data.turns) || data.turns.length === 0) {
        removeInlineQuestionCardFromDom(num, item.id);
        showToast("🗑 Cancelled and deleted in-flight question.");
        return;
      }

      item.turns = data.turns;
      const prevTurn = data.turns[data.turns.length - 1];
      item.message = prevTurn.question;
      item.reply = prevTurn.reply;
      item.reply_status = "answered";
      item.sending = false;
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
      showToast("🗑 Cancelled and deleted pending follow-up question.");
    } catch (err) {
      state.qaActionInFlightById.delete(item.id);
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
      showToast("Could not delete question: " + err);
    }
  }

  function openInlineFollowupComposer(clNum, itemId) {
    const item = findInlineItemById(clNum, itemId);
    if (!item) return;
    if (isQuestionThreadPending(item)) {
      showToast("Wait for the agent's reply before submitting another question in this conversation.");
      return;
    }
    state.openFollowupByItemId[item.id] = "";
    insertOrReplaceInlineItemInDom(clNum, item, {
      forceRender: true,
      focusTextareaId: `qaFollowupInput-${item.id}`,
    });
  }

  function cancelInlineFollowupComposer(clNum, itemId) {
    const item = findInlineItemById(clNum, itemId);
    delete state.openFollowupByItemId[itemId];
    if (item) {
      insertOrReplaceInlineItemInDom(clNum, item, { forceRender: true });
    }
  }

  async function submitInlineFollowupQuestion(clNum, itemId) {
    const num = Number(clNum);
    const item = findInlineItemById(num, itemId);
    if (!item) return;
    if (isQuestionThreadPending(item)) {
      showToast("Wait for the agent's reply before submitting another question in this conversation.");
      return;
    }
    const ta = document.getElementById(`qaFollowupInput-${item.id}`);
    const followupText = String((ta ? ta.value : state.openFollowupByItemId[item.id]) || "").trim();
    if (!followupText) {
      showToast("Enter a follow-up question first.");
      return;
    }

    delete state.openFollowupByItemId[item.id];
    const viewerLdap = (state.viewer || "you").split("@")[0];
    const turns = normalizeQuestionTurns(item);
    turns.push({
      turn_index: turns.length,
      question: followupText,
      reply: "",
      status: "thinking",
      author: viewerLdap,
    });
    item.turns = turns;
    item.participants = collectQuestionParticipants(item, turns);
    item.message = followupText;
    item.reply = "";
    item.reply_status = "thinking";
    item.sending = true;
    insertOrReplaceInlineItemInDom(num, item, { forceRender: true });

    try {
      const projectId = await resolveSidecarProjectId();
      const hasExistingConv = Boolean(item.conversation_id);
      const resp = await sidecarFetch("/api/gerrit/ask-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          file_path: item.file_path || "",
          start_line: item.start_line || item.line || 1,
          end_line: item.end_line || item.line || 1,
          side: item.side || "REVISION",
          code_snippet: item.code_snippet || "",
          question: followupText,
          conversationId: item.conversation_id || "",
          spawnNew: !hasExistingConv,
          threadId: item.thread_id || item.id,
          function_name: item.function_name || "",
          projectId,
          viewer: state.viewer || "",
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error((data && data.error) || `HTTP ${resp.status}`);

      item.sending = false;
      item.dispatched = Boolean(data && data.dispatched);
      if (data.conversation_id) item.conversation_id = data.conversation_id;
      if (Array.isArray(data.turns) && data.turns.length > 0) item.turns = data.turns;
      if (Array.isArray(data.participants) && data.participants.length > 0) item.participants = data.participants;
      insertOrReplaceInlineItemInDom(num, item, { forceRender: true });
      ensureCodeQuestionPollingTimer();
      showToast("🤖 Sent follow-up question in thread!");
    } catch (err) {
      item.turns = turns.slice(0, -1);
      const prev = item.turns[item.turns.length - 1];
      item.message = prev ? prev.question : "";
      item.reply = prev ? prev.reply : "";
      item.reply_status = "answered";
      item.sending = false;
      state.openFollowupByItemId[item.id] = followupText;
      insertOrReplaceInlineItemInDom(num, item, {
        forceRender: true,
        focusTextareaId: `qaFollowupInput-${item.id}`,
      });
      showToast("Could not send follow-up: " + err);
    }
  }

  function updatePgGutterDragPill(event, drag) {
    if (!drag || typeof document === "undefined") return;
    let pill = document.getElementById("pgGutterDragPill");
    if (!pill) {
      pill = document.createElement("div");
      pill.id = "pgGutterDragPill";
      pill.className = "pg-gutter-drag-pill";
      if (document.body) {
        document.body.appendChild(pill);
      }
    }
    const minIdx = Math.min(drag.startIdx, drag.currentIdx);
    const maxIdx = Math.max(drag.startIdx, drag.currentIdx);
    const count = maxIdx - minIdx + 1;
    const startLineEl = drag.tableEl ? drag.tableEl.querySelector(`.code-line[data-idx="${minIdx}"]`) : null;
    const endLineEl = drag.tableEl ? drag.tableEl.querySelector(`.code-line[data-idx="${maxIdx}"]`) : null;
    const startLn = startLineEl ? (startLineEl.getAttribute("data-new-ln") || startLineEl.getAttribute("data-old-ln") || (minIdx + 1)) : (minIdx + 1);
    const endLn = endLineEl ? (endLineEl.getAttribute("data-new-ln") || endLineEl.getAttribute("data-old-ln") || (maxIdx + 1)) : (maxIdx + 1);
    const rangeLabel = minIdx === maxIdx ? `L${startLn}` : `L${startLn}–L${endLn}`;
    const countLabel = count === 1 ? "1 line" : `${count} lines`;

    pill.innerHTML = `<span class="pg-gutter-drag-icon" aria-hidden="true">↕</span> <strong class="pg-gutter-drag-range">${escapeHtml(rangeLabel)}</strong> <span class="pg-gutter-drag-count">(${escapeHtml(countLabel)})</span> <span class="pg-gutter-drag-hint">· Release to comment</span>`;
    const clientX = (event && event.clientX != null) ? event.clientX : 0;
    const clientY = (event && event.clientY != null) ? event.clientY : 0;
    pill.style.left = `${Math.max(12, clientX + 16)}px`;
    pill.style.top = `${Math.max(12, clientY - 14)}px`;
    pill.style.display = "inline-flex";
  }

  function removePgGutterDragPill() {
    if (typeof document === "undefined") return;
    const pill = document.getElementById("pgGutterDragPill");
    if (pill) {
      pill.style.display = "none";
      if (typeof pill.remove === "function") {
        pill.remove();
      }
    }
  }

  function cancelPgGutterDrag() {
    if (!state.gutterDrag) return;
    const drag = state.gutterDrag;
    state.gutterDrag = null;
    state.suppressGutterClick = true;
    setTimeout(() => { state.suppressGutterClick = false; }, 150);
    if (typeof document !== "undefined" && document.body) {
      document.body.classList.remove("is-gutter-dragging");
    }
    const diffScrollEl = document.getElementById("diffScroll");
    if (diffScrollEl) {
      diffScrollEl.classList.remove("is-gutter-dragging");
    }
    removePgGutterDragPill();
    if (drag.tableEl) {
      drag.tableEl.querySelectorAll(".code-line.is-drag-preview").forEach(el => {
        el.classList.remove("code-line-selected", "is-drag-preview");
      });
    }
    deselectBrowserSelection();
    if (typeof showToast === "function") {
      showToast("Cancelled line selection");
    }
  }

  function initDiffCodeSelectionListeners() {
    const diffScrollEl = document.getElementById("diffScroll");
    if (!diffScrollEl) return;
    if (diffScrollEl._hasCodeSelectionListeners) return;
    diffScrollEl._hasCodeSelectionListeners = true;

    // Gutter drag selection start
    diffScrollEl.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      const lnEl = (event.target && event.target.closest) ? event.target.closest(".ln") : null;
      if (!lnEl) return;
      if (event.target && event.target.closest("#codeSelectionComposer, .diff-inline-comment, a, input, textarea")) {
        return;
      }
      const lineEl = lnEl.closest(".code-line[data-idx]");
      if (!lineEl) return;
      const tableEl = lineEl.closest(".code-table");
      if (!tableEl) return;
      const directIdx = lnEl.getAttribute("data-idx");
      const startIdx = (directIdx != null && directIdx !== "") ? Number(directIdx) : Number(lineEl.getAttribute("data-idx"));
      if (Number.isNaN(startIdx)) return;

      const clNum = Number(lineEl.getAttribute("data-cl") || tableEl.getAttribute("data-cl") || state.selectedClNumber || 0);
      const filePath = lineEl.getAttribute("data-file") || tableEl.getAttribute("data-file") || "";
      const side = lnEl.getAttribute("data-side") || (lnEl.classList.contains("ln-old") ? "left" : "right");

      state.gutterDrag = {
        active: false,
        isMouseDown: true,
        startIdx,
        currentIdx: startIdx,
        tableEl,
        lineEl,
        startX: event.clientX || 0,
        startY: event.clientY || 0,
        clNum,
        filePath,
        side,
      };
      if (window.getSelection) {
        try { window.getSelection().removeAllRanges(); } catch (_) {}
      }
    });

    const onDocMouseMove = (event) => {
      if (!state.gutterDrag || !state.gutterDrag.isMouseDown) return;
      const drag = state.gutterDrag;
      const deltaY = Math.abs((event.clientY || 0) - drag.startY);
      const deltaX = Math.abs((event.clientX || 0) - drag.startX);
      if (!drag.active && (deltaY >= 3 || deltaX >= 4)) {
        drag.active = true;
        if (typeof document !== "undefined" && document.body) {
          document.body.classList.add("is-gutter-dragging");
        }
        if (diffScrollEl) {
          diffScrollEl.classList.add("is-gutter-dragging");
        }
      }
      if (!drag.active) return;
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (window.getSelection) {
        try { window.getSelection().removeAllRanges(); } catch (_) {}
      }

      let hoverLineEl = (event.target && event.target.closest) ? event.target.closest(".code-line[data-idx]") : null;
      if (!hoverLineEl && typeof document !== "undefined" && typeof document.elementFromPoint === "function") {
        const elAtPoint = document.elementFromPoint(event.clientX, event.clientY);
        hoverLineEl = elAtPoint ? elAtPoint.closest(".code-line[data-idx]") : null;
      }

      if (hoverLineEl && hoverLineEl.closest(".code-table") === drag.tableEl) {
        const hoverIdx = Number(hoverLineEl.getAttribute("data-idx"));
        if (!Number.isNaN(hoverIdx)) {
          drag.currentIdx = hoverIdx;
        }
      }

      const minIdx = Math.min(drag.startIdx, drag.currentIdx);
      const maxIdx = Math.max(drag.startIdx, drag.currentIdx);
      if (drag.tableEl) {
        const lines = Array.from(drag.tableEl.querySelectorAll(".code-line[data-idx]"));
        lines.forEach(el => {
          const idx = Number(el.getAttribute("data-idx"));
          if (!Number.isNaN(idx) && idx >= minIdx && idx <= maxIdx) {
            el.classList.add("code-line-selected", "is-drag-preview");
          } else {
            el.classList.remove("code-line-selected", "is-drag-preview");
          }
        });
      }
      updatePgGutterDragPill(event, drag);
    };

    const onDocMouseUp = (event) => {
      if (!state.gutterDrag) return;
      const drag = state.gutterDrag;
      state.gutterDrag = null;
      if (typeof document !== "undefined" && document.body) {
        document.body.classList.remove("is-gutter-dragging");
      }
      if (diffScrollEl) {
        diffScrollEl.classList.remove("is-gutter-dragging");
      }
      removePgGutterDragPill();

      if (drag.active) {
        state.suppressGutterClick = true;
        setTimeout(() => { state.suppressGutterClick = false; }, 150);
        const minIdx = Math.min(drag.startIdx, drag.currentIdx);
        const maxIdx = Math.max(drag.startIdx, drag.currentIdx);
        selectCodeBlockRange(drag.tableEl, minIdx, maxIdx, {
          anchorIdx: drag.startIdx,
          focusInput: true,
          mode: "comment",
        });
        const targetFile = drag.filePath || "";
        const baseName = targetFile.split("/").pop() || targetFile || "file";
        const startLineEl = drag.tableEl ? drag.tableEl.querySelector(`.code-line[data-idx="${minIdx}"]`) : null;
        const endLineEl = drag.tableEl ? drag.tableEl.querySelector(`.code-line[data-idx="${maxIdx}"]`) : null;
        const startLn = startLineEl ? (startLineEl.getAttribute("data-new-ln") || startLineEl.getAttribute("data-old-ln") || (minIdx + 1)) : (minIdx + 1);
        const endLn = endLineEl ? (endLineEl.getAttribute("data-new-ln") || endLineEl.getAttribute("data-old-ln") || (maxIdx + 1)) : (maxIdx + 1);
        const lineRangeStr = minIdx === maxIdx ? `L${startLn}` : `L${startLn}–L${endLn}`;
        if (typeof showToast === "function") {
          showToast(`Selected lines ${lineRangeStr} in ${baseName} · Press Ctrl+Enter to save draft`);
        }
      }
    };

    if (typeof document !== "undefined") {
      document.addEventListener("mousemove", onDocMouseMove);
      document.addEventListener("mouseup", onDocMouseUp);
    }

    diffScrollEl.addEventListener("mouseup", (event) => {
      if (event.target && event.target.closest("#codeSelectionComposer, .ln, .diff-inline-comment, button, a, input, textarea")) {
        return;
      }
      const sel = window.getSelection ? window.getSelection() : null;
      if (!sel || sel.isCollapsed || !sel.toString().trim()) {
        if (state.codeSelection) {
          const inputEl = document.getElementById("codeBlockCommentInput") || document.getElementById("codeBlockAskInput");
          const curDraft = inputEl ? inputEl.value.trim() : (state.codeSelection.draft || "").trim();
          if (!curDraft) {
            closeCodeSelectionComposer(event, { discardDraft: true });
          }
        }
        return;
      }
      const findCodeLine = (node) => {
        if (!node) return null;
        const el = node.nodeType === 1 ? node : node.parentElement;
        return el ? el.closest(".code-line[data-idx]") : null;
      };
      const anchorLine = findCodeLine(sel.anchorNode);
      const focusLine = findCodeLine(sel.focusNode);
      if (!anchorLine && !focusLine) return;
      const startLineEl = anchorLine || focusLine;
      const endLineEl = focusLine || anchorLine;
      const tableA = startLineEl.closest(".code-table");
      const tableB = endLineEl.closest(".code-table");
      if (!tableA || tableA !== tableB) return;

      const idxA = Number(startLineEl.getAttribute("data-idx"));
      const idxB = Number(endLineEl.getAttribute("data-idx"));
      if (Number.isNaN(idxA) || Number.isNaN(idxB)) return;
      selectCodeBlockRange(tableA, idxA, idxB, { anchorIdx: idxA, focusInput: false });
    });

    diffScrollEl.addEventListener("click", (event) => {
      if (state.suppressGutterClick) return;
      if (event.target && event.target.closest("#codeSelectionComposer, .diff-inline-comment, .pg-inline-thread-card, button, a, input, textarea")) {
        return;
      }
      if (event.target && event.target.closest(".ln")) return;
      const lineEl = (event.target && event.target.closest) ? event.target.closest(".code-line[data-idx]") : null;
      if (!lineEl) return;
      if (lineEl.classList.contains("code-hunk") || lineEl.classList.contains("code-context-control")) return;
      const lnEl = lineEl.querySelector(".ln-new") || lineEl.querySelector(".ln-old") || lineEl.querySelector(".ln");
      if (lnEl) {
        onDiffLineGutterClick(event, lnEl);
      }
    });

    diffScrollEl.addEventListener("dblclick", (event) => {
      if (event.target && event.target.closest("#codeSelectionComposer, .diff-inline-comment, button, a, input, textarea")) {
        return;
      }
      const lineEl = (event.target && event.target.closest) ? event.target.closest(".code-line[data-idx]") : null;
      if (!lineEl) return;
      const tableEl = lineEl.closest(".code-table");
      if (!tableEl) return;
      const idx = Number(lineEl.getAttribute("data-idx"));
      if (Number.isNaN(idx)) return;
      event.preventDefault();
      if (window.getSelection) {
        try { window.getSelection().removeAllRanges(); } catch (_) {}
      }
      selectCodeBlockRange(tableEl, idx, idx, { anchorIdx: idx, focusInput: true, mode: "comment" });
      const targetFile = lineEl.getAttribute("data-file") || "";
      const baseName = targetFile.split("/").pop() || targetFile;
      const ln = lineEl.getAttribute("data-new-ln") || lineEl.getAttribute("data-old-ln") || (idx + 1);
      if (typeof showToast === "function") {
        showToast(`Add comment on ${baseName} (L${ln}) · Press Ctrl+Enter to save draft`);
      }
    });
  }

  if (typeof window !== "undefined") {
    window.isPgDiffNavContext = isPgDiffNavContext;
    window.navigatePgDiffLine = navigatePgDiffLine;
    window.stepPgDiffLineSelection = stepPgDiffLineSelection;
    window.switchPgDiffSide = switchPgDiffSide;
    window.selectCodeBlockRange = selectCodeBlockRange;
    window.buildCodeSelectionComposerInnerHtml = buildCodeSelectionComposerInnerHtml;
    window.mountCodeSelectionComposer = mountCodeSelectionComposer;
    window.clearCodeSelection = clearCodeSelection;
    window.closeCodeSelectionComposer = closeCodeSelectionComposer;
    window.deselectBrowserSelection = deselectBrowserSelection;
    window.setCodeSelectionMode = setCodeSelectionMode;
    window.onComposerModeTabKeydown = onComposerModeTabKeydown;
    window.applyQuickReviewComment = applyQuickReviewComment;
    window.applyQuickCodeQuestion = applyQuickCodeQuestion;
    window.onCodeComposerKeydown = onCodeComposerKeydown;
    window.updatePgGutterDragPill = updatePgGutterDragPill;
    window.removePgGutterDragPill = removePgGutterDragPill;
    window.cancelPgGutterDrag = cancelPgGutterDrag;
    window.onDiffLineGutterClick = onDiffLineGutterClick;
    window.triggerPgLineComment = triggerPgLineComment;
    window.extractSelectedBlockMetadata = extractSelectedBlockMetadata;
    window.renderInlineLineCommentsHtml = renderInlineLineCommentsHtml;
    window.initDiffCodeSelectionListeners = initDiffCodeSelectionListeners;
    window.getLineThreadIndicatorClass = getLineThreadIndicatorClass;
    window.getPgLineCommentSummary = getPgLineCommentSummary;
    window.formatPgGutterCommentTooltip = formatPgGutterCommentTooltip;
    window.renderPgGutterCommentBadgeHtml = renderPgGutterCommentBadgeHtml;
    window.copySelectedCodeSnippet = copySelectedCodeSnippet;
    window.copySelectedCodePermalink = copySelectedCodePermalink;
    window.copySelectedCodeMarkdown = copySelectedCodeMarkdown;
    window.onPgGutterCommentBadgeClick = onPgGutterCommentBadgeClick;
    window.onPgGutterCommentBadgeKeyDown = onPgGutterCommentBadgeKeyDown;
  }

  function copyAllWaitingPings() {
    const waiting = state.changes.filter(c => c.approved_by_viewer);
    const text = waiting.map(c => `• ag/${c.number}: ${c.subject} (${c.url})`).join("\n");
    navigator.clipboard?.writeText(text || "No waiting CLs.");
    showToast(`Copied follow-up digest for ${waiting.length} CLs!`);
  }

  function resolveNextTargetAfterVote(votedNums) {
    const votedSet = new Set((votedNums || []).map(x => Number(x)));
    const orderedItems = getOrderedVisibleCommits(getVisibleStacks());
    const flatVisible = orderedItems.map(item => ({
      stackId: item.stack.stack_id,
      clNumber: Number(item.commit.number),
      stackCommits: item.stack.commits.map(x => Number(x.number)).filter(num => !votedSet.has(num)),
    }));
    if (!flatVisible.length) return null;
    const curIdx = flatVisible.findIndex(item => item.clNumber === Number(state.selectedClNumber));
    if (curIdx >= 0 && !votedSet.has(flatVisible[curIdx].clNumber)) {
      return flatVisible[curIdx];
    }
    const startIdx = curIdx >= 0 ? curIdx : 0;
    for (let offset = 1; offset <= flatVisible.length; offset++) {
      const cand = flatVisible[(startIdx + offset) % flatVisible.length];
      if (!votedSet.has(cand.clNumber)) {
        return cand;
      }
    }
    return null;
  }

  function syncLocalParticipantVoteBadge(c, labelName, shortLabel, val) {
    const viewerEmail = (state.viewer || "").toLowerCase();
    const viewerShort = (state.viewer || "you").split("@")[0];
    if (!Array.isArray(c.reviewers_detail)) {
      c.reviewers_detail = getFallbackReviewersForCl(c);
    }
    let me = c.reviewers_detail.find(p => (p.email || "").toLowerCase() === viewerEmail);
    if (!me && val !== 0) {
      me = {
        email: viewerEmail,
        short: viewerShort,
        name: viewerShort,
        full_name: viewerShort,
        state: "REVIEWER",
        is_bot: false,
        is_owner: viewerEmail === (c.owner_email || "").toLowerCase(),
        is_viewer: true,
        removable: viewerEmail !== (c.owner_email || "").toLowerCase(),
        cr_vote: 0,
        votes: [],
      };
      c.reviewers_detail.unshift(me);
    }
    if (!me) return;
    if (labelName === "Code-Review") me.cr_vote = val;
    const nextVotes = (me.votes || []).filter(v => v.label !== labelName);
    if (val !== 0) {
      const entry = { label: labelName, short_label: shortLabel, value: val, formatted: val > 0 ? `+${val}` : String(val) };
      if (labelName === "Code-Review") nextVotes.unshift(entry);
      else nextVotes.push(entry);
    }
    me.votes = nextVotes;
  }

  function applyOptimisticPresubmitReadyToCl(c, viewerShort, presubmitReady) {
    const prVal = presubmitReady === true ? 1 : (presubmitReady === false ? 0 : Number(presubmitReady));
    c.viewer_presubmit_ready = prVal >= 1;
    const others = (c.presubmit_ready_voters || []).filter(x => x !== viewerShort);
    if (prVal >= 1) others.push(viewerShort);
    c.presubmit_ready_voters = others;
    c.presubmit_ready_enabled = others.length > 0;
    if (prVal >= 1) {
      c.presubmit_status = "queued";
      c.presubmit_label = "⏳ PV Queued";
    } else if (c.presubmit_status === "queued" && !c.presubmit_ready_enabled) {
      const fb = resolveFallbackPresubmitStatusAndLabel(c);
      c.presubmit_status = fb.status;
      c.presubmit_label = fb.label;
    }
    syncLocalParticipantVoteBadge(c, "Presubmit-Ready", "PR", prVal);
    c.is_submittable = isClSubmittable(c);
  }

  function applyOptimisticVoteToCl(
    c, codeReview, autosubmit, presubmitReady, viewerOverride = null, verified = null,
    performanceOpt = null, lintOpt = null, bypassPresubmitOpt = null, apiReviewOpt = null, ownersOverrideOpt = null,
    presubmitVerifiedOpt = null, rubberStampOpt = null, commitQueueOpt = null, openSourceLicensingOpt = null, trustOpt = null, extraLabelsOpt = null
  ) {
    const viewerEmail = (viewerOverride || state.viewer || "").toLowerCase();
    const viewerShort = (viewerOverride || state.viewer || "you").split("@")[0];
    if (viewerEmail) {
      c.viewer_in_attention_set = false;
      if (Array.isArray(c.attention_set_emails)) {
        c.attention_set_emails = c.attention_set_emails.filter(
          em => String(em || "").toLowerCase() !== viewerEmail
        );
      }
      if (Array.isArray(c.reviewers_detail)) {
        c.reviewers_detail.forEach(p => {
          if (String(p.email || "").toLowerCase() === viewerEmail) {
            p.in_attention_set = false;
          }
        });
      }
    }
    if (codeReview !== null && codeReview !== undefined) {
      const crVal = Number(codeReview);
      c.viewer_cr_vote = crVal;
      syncLocalParticipantVoteBadge(c, "Code-Review", "CR", crVal);
      if (crVal >= 2) {
        c.approved_by_viewer = true;
        c.human_cr2 = c.human_cr2 || [];
        if (!c.human_cr2.some(v => (v.email || "").toLowerCase() === viewerEmail)) {
          c.human_cr2.push({ email: state.viewer, name: viewerShort, short: viewerShort, value: 2 });
        }
      } else {
        c.approved_by_viewer = false;
        if (Array.isArray(c.human_cr2)) {
          c.human_cr2 = c.human_cr2.filter(v => (v.email || "").toLowerCase() !== viewerEmail);
        }
      }
      c.human_cr2_count = Array.isArray(c.human_cr2) ? c.human_cr2.length : 0;
      c.approval_slots = recomputeApprovalSlotsForViewer(c, viewerEmail);
      c.gerrit_submittable = null;
      c.is_submittable = isClSubmittable(c);
    }
    if (verified !== null && verified !== undefined) {
      const vVal = Number(verified);
      c.viewer_verified_vote = vVal;
      syncLocalParticipantVoteBadge(c, "Verified", "V", vVal);
    }
    if (autosubmit !== null && autosubmit !== undefined) {
      const asVal = autosubmit === true ? 1 : (autosubmit === false ? 0 : Number(autosubmit));
      c.viewer_autosubmit = asVal >= 1;
      const others = (c.autosubmit_voters || []).filter(x => x !== viewerShort);
      if (asVal >= 1) others.push(viewerShort);
      c.autosubmit_voters = others;
      c.autosubmit_enabled = others.length > 0;
      syncLocalParticipantVoteBadge(c, "Autosubmit", "AS", asVal);
    }
    if (presubmitReady !== null && presubmitReady !== undefined) {
      applyOptimisticPresubmitReadyToCl(c, viewerShort, presubmitReady);
    }
    if (performanceOpt !== null && performanceOpt !== undefined) {
      const perfVal = Number(performanceOpt);
      c.viewer_performance_vote = perfVal;
      syncLocalParticipantVoteBadge(c, "Performance", "Perf", perfVal);
    }
    if (lintOpt !== null && lintOpt !== undefined) {
      const lintVal = Number(lintOpt);
      c.viewer_lint_vote = lintVal;
      syncLocalParticipantVoteBadge(c, "Lint", "Lint", lintVal);
    }
    if (bypassPresubmitOpt !== null && bypassPresubmitOpt !== undefined) {
      const bpVal = Number(bypassPresubmitOpt);
      c.viewer_bypass_presubmit_vote = bpVal;
      syncLocalParticipantVoteBadge(c, "Bypass-Presubmit", "BP", bpVal);
    }
    if (apiReviewOpt !== null && apiReviewOpt !== undefined) {
      const apiVal = Number(apiReviewOpt);
      c.viewer_api_review_vote = apiVal;
      syncLocalParticipantVoteBadge(c, "API-Review", "API", apiVal);
    }
    if (ownersOverrideOpt !== null && ownersOverrideOpt !== undefined) {
      const ooVal = Number(ownersOverrideOpt);
      c.viewer_owners_override_vote = ooVal;
      syncLocalParticipantVoteBadge(c, "Owners-Override", "OO", ooVal);
    }
    if (presubmitVerifiedOpt !== null && presubmitVerifiedOpt !== undefined) {
      const pvVal = Number(presubmitVerifiedOpt);
      c.viewer_presubmit_verified_vote = pvVal;
      syncLocalParticipantVoteBadge(c, "Presubmit-Verified", "PV", pvVal);
    }
    if (rubberStampOpt !== null && rubberStampOpt !== undefined) {
      const rsVal = Number(rubberStampOpt);
      c.viewer_rubber_stamp_vote = rsVal;
      syncLocalParticipantVoteBadge(c, "Rubber-Stamp", "RS", rsVal);
    }
    if (commitQueueOpt !== null && commitQueueOpt !== undefined) {
      const cqVal = Number(commitQueueOpt);
      c.viewer_commit_queue_vote = cqVal;
      syncLocalParticipantVoteBadge(c, "Commit-Queue", "CQ", cqVal);
    }
    if (openSourceLicensingOpt !== null && openSourceLicensingOpt !== undefined) {
      const oslVal = Number(openSourceLicensingOpt);
      c.viewer_open_source_licensing_vote = oslVal;
      syncLocalParticipantVoteBadge(c, "Open-Source-Licensing", "OSL", oslVal);
    }
    if (trustOpt !== null && trustOpt !== undefined) {
      const trustVal = Number(trustOpt);
      c.viewer_trust_vote = trustVal;
      syncLocalParticipantVoteBadge(c, "Trust", "Trust", trustVal);
    }
    if (extraLabelsOpt && typeof extraLabelsOpt === "object") {
      Object.entries(extraLabelsOpt).forEach(([lbl, val]) => {
        if (val !== null && val !== undefined) {
          const numVal = Number(val);
          syncLocalParticipantVoteBadge(c, lbl, lbl, numVal);
        }
      });
    }
  }

  function markClsVotedOptimistically(cls, voteOpts = {}) {
    if (!voteOpts.skipCacheInvalidate) {
      invalidateClientQueueCache();
    }
    const cr = voteOpts.codeReview;
    const vOpt = voteOpts.verified;
    const asOpt = voteOpts.autosubmit;
    const prOpt = voteOpts.presubmitReady;
    const perfOpt = voteOpts.performance;
    const lintOpt = voteOpts.lint;
    const bpOpt = voteOpts.bypassPresubmit;
    const apiOpt = voteOpts.apiReview;
    const ooOpt = voteOpts.ownersOverride;
    const pvOpt = voteOpts.presubmitVerified;
    const rsOpt = voteOpts.rubberStamp;
    const cqOpt = voteOpts.commitQueue;
    const oslOpt = voteOpts.openSourceLicensing;
    const trustOpt = voteOpts.trust;
    const extraLabelsOpt = voteOpts.labels;
    const targetNums = new Set();
    (cls || []).forEach(rawNum => {
      const num = Number(rawNum);
      if (Number.isNaN(num) || num <= 0) return;
      targetNums.add(num);
      if (cr !== null && cr !== undefined) {
        const sessKey = makeSessionApprovalKey(state.viewer, num);
        if (Number(cr) >= 2) state.sessionApprovedCls.add(sessKey);
        else state.sessionApprovedCls.delete(sessKey);
      }
      if (prOpt !== null && prOpt !== undefined) {
        delete state.presubmitTargetsByCl[num];
      }
    });
    const visit = (c) => {
      if (c && targetNums.has(Number(c.number))) {
        applyOptimisticVoteToCl(
          c, cr, asOpt, prOpt, null, vOpt, perfOpt, lintOpt, bpOpt, apiOpt, ooOpt,
          pvOpt, rsOpt, cqOpt, oslOpt, trustOpt, extraLabelsOpt
        );
      }
    };
    state.changes.forEach(visit);
    state.stacks.forEach(st => (st.commits || []).forEach(visit));
  }

  function markClsApprovedOptimistically(cls, extraOpts = {}) {
    markClsVotedOptimistically(cls, { codeReview: 2, ...extraOpts });
  }

  function formatVoteLabelsText(
    codeReview, autosubmit, presubmitReady, verified = null, performance = null,
    lint = null, bypassPresubmit = null, apiReview = null, ownersOverride = null,
    presubmitVerified = null, rubberStamp = null, commitQueue = null,
    openSourceLicensing = null, trust = null, extraLabels = null
  ) {
    const parts = [];
    const shortParts = [];
    if (codeReview !== null && codeReview !== undefined) {
      const cr = Number(codeReview);
      const crFmt = cr > 0 ? `+${cr}` : String(cr);
      parts.push(`Code-Review ${crFmt}`);
      shortParts.push(cr === 0 ? "CR 0" : crFmt);
    }
    if (verified !== null && verified !== undefined) {
      const v = Number(verified);
      const vFmt = v > 0 ? `+${v}` : String(v);
      parts.push(`Verified ${vFmt}`);
      shortParts.push(v === 0 ? "V 0" : `V ${vFmt}`);
    }
    if (autosubmit !== null && autosubmit !== undefined && autosubmit !== false) {
      const asVal = autosubmit === true ? 1 : Number(autosubmit);
      const asFmt = asVal > 0 ? `+${asVal}` : String(asVal);
      parts.push(`Autosubmit ${asFmt}`);
      shortParts.push(`Autosubmit ${asFmt}`);
    }
    if (presubmitReady !== null && presubmitReady !== undefined && presubmitReady !== false) {
      const prVal = presubmitReady === true ? 1 : Number(presubmitReady);
      const prFmt = prVal > 0 ? `+${prVal}` : String(prVal);
      parts.push(`Presubmit-Ready ${prFmt}`);
      shortParts.push(`Presubmit-Ready ${prFmt}`);
    }
    if (performance !== null && performance !== undefined) {
      const perf = Number(performance);
      const perfFmt = perf > 0 ? `+${perf}` : String(perf);
      parts.push(`Performance ${perfFmt}`);
      shortParts.push(perf === 0 ? "Perf 0" : `Perf ${perfFmt}`);
    }
    if (lint !== null && lint !== undefined) {
      const l = Number(lint);
      const lFmt = l > 0 ? `+${l}` : String(l);
      parts.push(`Lint ${lFmt}`);
      shortParts.push(l === 0 ? "Lint 0" : `Lint ${lFmt}`);
    }
    if (bypassPresubmit !== null && bypassPresubmit !== undefined) {
      const bp = Number(bypassPresubmit);
      const bpFmt = bp > 0 ? `+${bp}` : String(bp);
      parts.push(`Bypass-Presubmit ${bpFmt}`);
      shortParts.push(bp === 0 ? "BP 0" : `BP ${bpFmt}`);
    }
    if (apiReview !== null && apiReview !== undefined) {
      const api = Number(apiReview);
      const apiFmt = api > 0 ? `+${api}` : String(api);
      parts.push(`API-Review ${apiFmt}`);
      shortParts.push(api === 0 ? "API 0" : `API ${apiFmt}`);
    }
    if (ownersOverride !== null && ownersOverride !== undefined) {
      const oo = Number(ownersOverride);
      const ooFmt = oo > 0 ? `+${oo}` : String(oo);
      parts.push(`Owners-Override ${ooFmt}`);
      shortParts.push(oo === 0 ? "OO 0" : `OO ${ooFmt}`);
    }
    if (presubmitVerified !== null && presubmitVerified !== undefined) {
      const pv = Number(presubmitVerified);
      const pvFmt = pv > 0 ? `+${pv}` : String(pv);
      parts.push(`Presubmit-Verified ${pvFmt}`);
      shortParts.push(pv === 0 ? "PV 0" : `PV ${pvFmt}`);
    }
    if (rubberStamp !== null && rubberStamp !== undefined) {
      const rs = Number(rubberStamp);
      const rsFmt = rs > 0 ? `+${rs}` : String(rs);
      parts.push(`Rubber-Stamp ${rsFmt}`);
      shortParts.push(rs === 0 ? "RS 0" : `RS ${rsFmt}`);
    }
    if (commitQueue !== null && commitQueue !== undefined) {
      const cq = Number(commitQueue);
      const cqFmt = cq > 0 ? `+${cq}` : String(cq);
      parts.push(`Commit-Queue ${cqFmt}`);
      shortParts.push(cq === 0 ? "CQ 0" : `CQ ${cqFmt}`);
    }
    if (openSourceLicensing !== null && openSourceLicensing !== undefined) {
      const osl = Number(openSourceLicensing);
      const oslFmt = osl > 0 ? `+${osl}` : String(osl);
      parts.push(`Open-Source-Licensing ${oslFmt}`);
      shortParts.push(osl === 0 ? "OSL 0" : `OSL ${oslFmt}`);
    }
    if (trust !== null && trust !== undefined) {
      const tr = Number(trust);
      const trFmt = tr > 0 ? `+${tr}` : String(tr);
      parts.push(`Trust ${trFmt}`);
      shortParts.push(tr === 0 ? "Trust 0" : `Trust ${trFmt}`);
    }
    if (extraLabels && typeof extraLabels === "object") {
      Object.entries(extraLabels).forEach(([lbl, val]) => {
        if (val !== null && val !== undefined) {
          const num = Number(val);
          const fmt = num > 0 ? `+${num}` : String(num);
          parts.push(`${lbl} ${fmt}`);
          shortParts.push(`${lbl} ${fmt}`);
        }
      });
    }
    return {
      full: parts.join(" & ") || "Vote",
      short: shortParts.join(" & ") || "Vote",
    };
  }

  function showVoteStampBanner(votedCls, nextTarget, shortVoteText) {
    const banner = document.getElementById("voteStampBanner");
    if (!banner) return;
    const votedLabel = votedCls.length === 1
      ? `ag/${votedCls[0]}`
      : `${votedCls.length} Commits`;
    const nextLabel = nextTarget
      ? `→ Next: ag/${nextTarget.clNumber}`
      : "→ Queue Cleared! 🎉";
    banner.textContent = `✓ Voted ${shortVoteText || "+2"} on ${votedLabel} ${nextLabel}`;
    banner.classList.remove("active");
    void banner.offsetWidth;
    banner.classList.add("active");
    clearTimeout(window._stampBannerTimer);
    window._stampBannerTimer = setTimeout(() => {
      banner.classList.remove("active");
    }, 1400);
  }

  function setVoteButtonsBusy(busy, activeBtn, labelHtml) {
    setToolbarVoteButtonsDisabled(busy);
    const resetBtn = document.getElementById("btnResetStagedVotes");
    if (resetBtn) resetBtn.disabled = busy;
    const submitBtn = document.getElementById("btnSubmitVotes");
    if (!busy && submitBtn) {
      submitBtn.classList.remove("is-voting", "is-voted");
    }
    if (busy && activeBtn) {
      activeBtn.disabled = true;
      activeBtn.classList.add("is-voting");
      activeBtn.innerHTML = `<span class="btn-spinner"></span> ${labelHtml}`;
    } else if (!busy && activeBtn) {
      activeBtn.classList.remove("is-voting", "is-voted");
    }
  }

  const sleepMs = (ms) => new Promise(resolve => setTimeout(resolve, ms));

  function handleVoteHttpFailure(cls, data) {
    state.votingInFlight = false;
    syncGlobalLoadingBar();
    setVoteButtonsBusy(false);
    ensureActiveDiffsLoaded({ preserveScroll: true });
    if (data && data.cloudtop_cmd) {
      navigator.clipboard?.writeText(data.cloudtop_cmd);
    }
    if (cls.length === 1) {
      const clObj = state.changes.find(c => Number(c.number) === cls[0]);
      if (clObj && clObj.url) {
        window.open(clObj.url, "_blank", "noopener,noreferrer");
      }
    }
    showToast(
      ((data && data.error) || "Vote failed") +
      (data && data.cloudtop_cmd ? " (Cloudtop command copied to clipboard!)" : "")
    );
  }

  function scheduleSilentPostVoteRefresh() {
    clearTimeout(window._postVoteQueueRefreshTimer);
    window._postVoteQueueRefreshTimer = setTimeout(() => {
      fetchQueueData(state.viewer, state.scope, {
        preserveDiffCache: true,
        preserveScroll: true,
        silent: true,
      });
    }, 1200);
  }

  async function castInPlaceVoteFlow(cls, activeBtn, reqBody, targetDesc, labelsText, voteOpts) {
    const origBtnHtml = activeBtn ? activeBtn.innerHTML : "";
    setVoteButtonsBusy(true, activeBtn, `${escapeHtml(labelsText.short)}…`);
    document.getElementById("drawerStatus").textContent = `Posting ${labelsText.full} on ${targetDesc} (notify: OWNER)…`;
    const resp = await sidecarFetch("/api/gerrit/vote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reqBody),
    });
    const data = await resp.json();
    if (!resp.ok || data.error || data.status !== "ok") {
      if (activeBtn && origBtnHtml) activeBtn.innerHTML = origBtnHtml;
      handleVoteHttpFailure(cls, data);
      return;
    }
    clearStagedVoteFieldsAfterSend();
    markClsVotedOptimistically(cls, voteOpts);
    if (voteOpts.presubmitReady && Number(voteOpts.presubmitReady) >= 1 && cls.includes(Number(state.selectedClNumber))) {
      state.expandedPresubmitCls.add(Number(state.selectedClNumber));
    }
    state.votingInFlight = false;
    syncGlobalLoadingBar();
    setVoteButtonsBusy(false);
    cls.forEach(num => rerenderReviewersPanelDom(num));
    if (state.checkedClNumbers.size > 0) {
      renderDiffPane({ preserveScroll: true });
    } else {
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
    }
    document.getElementById("drawerStatus").textContent = `Voted ${labelsText.full} on ${targetDesc} (notify: OWNER)`;
    showToast(`Voted ${labelsText.full} on ${cls.map(x => "ag/" + x).join(", ")} (notify: OWNER)!`);
    if (voteOpts.presubmitReady && Number(voteOpts.presubmitReady) >= 1 && cls.includes(Number(state.selectedClNumber))) {
      const activeNum = Number(state.selectedClNumber);
      setTimeout(() => fetchPresubmitTargetsForCl(activeNum, { forceRefresh: true }), 2200);
      ensurePresubmitTargetsPollingTimer();
    }
    scheduleSilentPostVoteRefresh();
  }

  function animateExitingApprovedCards(cls, activeClWasVoted) {
    cls.forEach(num => {
      document.querySelectorAll(`.cl-card[data-cl-num="${num}"]`).forEach(cardEl => {
        cardEl.classList.add("cl-card-exiting");
        const boxEl = cardEl.closest(".stack-box");
        if (boxEl && boxEl.querySelectorAll(".cl-card:not(.cl-card-exiting)").length === 0) {
          boxEl.classList.add("stack-box-exiting");
        }
      });
    });
    if (!activeClWasVoted) return;
    const diffEl = document.getElementById("diffScroll");
    if (diffEl) {
      diffEl.classList.remove("diff-enter-next", "diff-enter-prev");
      diffEl.classList.add("diff-exit-approved");
    }
  }

  function finalizeApprovedQueueTransition(cls, nextTarget, voteOpts, activeClWasVoted) {
    state.queueClearedCount += cls.length;
    state.explicitRadarSelection = false;
    cls.forEach(num => state.checkedClNumbers.delete(Number(num)));
    clearStagedVoteFieldsAfterSend();
    markClsVotedOptimistically(cls, voteOpts);
    syncGateControls();
    state.selectedStackId = nextTarget ? nextTarget.stackId : null;
    state.selectedClNumber = nextTarget ? nextTarget.clNumber : null;
    state.justEnteredClNumber = (activeClWasVoted && nextTarget) ? nextTarget.clNumber : null;
    state.votingInFlight = false;
    syncGlobalLoadingBar();
    setVoteButtonsBusy(false);
    renderAll({ preserveScroll: !activeClWasVoted });
    cls.forEach(num => {
      document.querySelectorAll(`.cl-card[data-cl-num="${num}"]`).forEach(staleCard => {
        if (state.gates.hideMine) {
          const boxEl = staleCard.closest(".stack-box");
          staleCard.remove();
          if (boxEl && boxEl.querySelectorAll(".cl-card").length === 0 && !boxEl.hasAttribute("data-pg-section")) {
            boxEl.remove();
          }
        }
      });
    });
    if (activeClWasVoted) {
      const diffEl = document.getElementById("diffScroll");
      if (diffEl) diffEl.scrollTop = 0;
      triggerDiffEnterAnimation("diff-enter-next");
      scrollSelectedCardIntoView();
      setTimeout(() => { state.justEnteredClNumber = null; }, 600);
    }
  }

  async function castVotes(rawCls, triggerBtn, voteOpts = {}) {
    const cls = (rawCls || []).map(x => Number(x)).filter(x => !Number.isNaN(x) && x > 0);
    if (!cls.length || state.votingInFlight) return;
    const cr = voteOpts.codeReview === undefined ? 2 : voteOpts.codeReview;
    const vOpt = voteOpts.verified;
    const asOpt = voteOpts.autosubmit;
    const prOpt = voteOpts.presubmitReady;
    const perfOpt = voteOpts.performance;
    const lintOpt = voteOpts.lint;
    const bpOpt = voteOpts.bypassPresubmit;
    const apiOpt = voteOpts.apiReview;
    const ooOpt = voteOpts.ownersOverride;
    const pvOpt = voteOpts.presubmitVerified;
    const rsOpt = voteOpts.rubberStamp;
    const cqOpt = voteOpts.commitQueue;
    const oslOpt = voteOpts.openSourceLicensing;
    const trustOpt = voteOpts.trust;
    const extraLabels = voteOpts.labels || {};
    const msg = String(voteOpts.message || "").trim();
    const labelsText = formatVoteLabelsText(
      cr, asOpt, prOpt, vOpt, perfOpt, lintOpt, bpOpt, apiOpt, ooOpt,
      pvOpt, rsOpt, cqOpt, oslOpt, trustOpt, extraLabels
    );
    const reqBody = { cls, code_review: cr };
    if (vOpt !== null && vOpt !== undefined) {
      reqBody.verified = Number(vOpt);
    }
    if (asOpt !== undefined && asOpt !== null && asOpt !== false) {
      reqBody.autosubmit = asOpt === true ? 1 : Number(asOpt);
    }
    if (prOpt !== undefined && prOpt !== null && prOpt !== false) {
      reqBody.presubmit_ready = prOpt === true ? 1 : Number(prOpt);
    }
    if (perfOpt !== null && perfOpt !== undefined) {
      reqBody.performance = Number(perfOpt);
    }
    if (lintOpt !== null && lintOpt !== undefined) {
      reqBody.lint = Number(lintOpt);
    }
    if (bpOpt !== null && bpOpt !== undefined) {
      reqBody.bypass_presubmit = Number(bpOpt);
    }
    if (apiOpt !== null && apiOpt !== undefined) {
      reqBody.api_review = Number(apiOpt);
    }
    if (ooOpt !== null && ooOpt !== undefined) {
      reqBody.owners_override = Number(ooOpt);
    }
    if (pvOpt !== null && pvOpt !== undefined) {
      reqBody.presubmit_verified = Number(pvOpt);
    }
    if (rsOpt !== null && rsOpt !== undefined) {
      reqBody.rubber_stamp = Number(rsOpt);
    }
    if (cqOpt !== null && cqOpt !== undefined) {
      reqBody.commit_queue = Number(cqOpt);
    }
    if (oslOpt !== null && oslOpt !== undefined) {
      reqBody.open_source_licensing = Number(oslOpt);
    }
    if (trustOpt !== null && trustOpt !== undefined) {
      reqBody.trust = Number(trustOpt);
    }
    if (extraLabels && Object.keys(extraLabels).length > 0) {
      reqBody.labels = { ...(reqBody.labels || {}), ...extraLabels };
    }
    if (msg) {
      reqBody.message = msg;
    }
    const targetDesc = cls.length === 1 ? `ag/${cls[0]}` : `${cls.length} CLs`;
    const activeBtn = triggerBtn || document.getElementById("btnSubmitVotes");
    const shouldAdvanceQueue = cr !== null && Number(cr) >= 2 && Boolean(state.gates.hideMine) && state.scope !== "owner";
    const activeClWasVoted = cls.includes(Number(state.selectedClNumber));
    const normOpts = {
      codeReview: cr,
      verified: vOpt,
      presubmitVerified: pvOpt,
      autosubmit: asOpt,
      presubmitReady: prOpt,
      performance: perfOpt,
      lint: lintOpt,
      bypassPresubmit: bpOpt,
      apiReview: apiOpt,
      ownersOverride: ooOpt,
      rubberStamp: rsOpt,
      commitQueue: cqOpt,
      openSourceLicensing: oslOpt,
      trust: trustOpt,
      labels: extraLabels,
      message: msg,
    };

    state.votingInFlight = true;
    syncGlobalLoadingBar();
    try {
      if (!shouldAdvanceQueue) {
        await castInPlaceVoteFlow(cls, activeBtn, reqBody, targetDesc, labelsText, normOpts);
        return;
      }
      syncGateControls();
      setVoteButtonsBusy(true, activeBtn, `Sending ${escapeHtml(labelsText.short)} to ${escapeHtml(targetDesc)}…`);
      document.getElementById("drawerStatus").textContent = `Posting ${labelsText.full} on ${targetDesc} (notify: OWNER)…`;

      const nextTarget = resolveNextTargetAfterVote(cls);
      const prefetchNums = nextTarget ? [nextTarget.clNumber] : [];
      const prefetchPromise = prefetchDiffsForNumbers(prefetchNums);
      const resp = await sidecarFetch("/api/gerrit/vote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reqBody),
      });
      const data = await resp.json();
      if (!resp.ok || data.error || data.status !== "ok") {
        handleVoteHttpFailure(cls, data);
        return;
      }
      if (activeBtn) {
        activeBtn.classList.remove("is-voting");
        activeBtn.classList.add("is-voted");
        activeBtn.textContent = `✓ ${labelsText.short} Sent!`;
      }
      showVoteStampBanner(cls, nextTarget, labelsText.short);
      animateExitingApprovedCards(cls, activeClWasVoted);
      await Promise.all([sleepMs(210), prefetchPromise]);
      finalizeApprovedQueueTransition(cls, nextTarget, normOpts, activeClWasVoted);
      document.getElementById("drawerStatus").textContent = nextTarget
        ? `Voted ${labelsText.short} on ${targetDesc} · Now reviewing ag/${nextTarget.clNumber}`
        : `Voted ${labelsText.short} on ${targetDesc} · Queue cleared!`;
      showToast(`Voted ${labelsText.full} on ${cls.map(x => "ag/" + x).join(", ")} (notify: OWNER)!`);
      scheduleSilentPostVoteRefresh();
    } catch (err) {
      state.votingInFlight = false;
      syncGlobalLoadingBar();
      setVoteButtonsBusy(false);
      await ensureActiveDiffsLoaded({ preserveScroll: true });
      showToast("Vote request failed: " + err);
    }
  }

  function getSwarmElapsedSec(swarmObj) {
    if (!swarmObj) return 0;
    if (swarmObj.startedAtClientMs) {
      return Math.max(0, Math.round((Date.now() - swarmObj.startedAtClientMs) / 1000));
    }
    return Math.max(0, Math.round(Number(swarmObj.elapsed_sec || 0)));
  }

  function renderClSwarmBadgeHtml(clNum) {
    const sw = state.swarmByCl[clNum];
    if (!sw) return "";
    return window.AemuGerritReact.renderClSwarmBadgeHtml(sw, getSwarmElapsedSec(sw));
  }

  function renderSpecialistChipsHtml(agents, isRunning) {
    return window.AemuGerritReact.renderSpecialistChipsHtml(
      agents,
      Boolean(isRunning),
      DEFAULT_SWARM_SPECIALISTS
    );
  }

  function renderSwarmFindingsListHtml(clNum, findings) {
    if (!Array.isArray(findings) || findings.length === 0) return "";
    const enriched = findings.map(f => {
      const loc = `${f.file || "/COMMIT_MSG"}:${f.line || 1}`;
      const locHtml = (f.file && f.file !== "/COMMIT_MSG")
        ? renderCodeSearchFileLinkHtml(clNum, f.file, loc, f.line || 1)
        : escapeHtml(loc);
      return Object.assign({}, f, { locHtml });
    });
    return window.AemuGerritReact.renderSwarmFindingsListHtml(clNum, enriched);
  }

  function renderCommitSwarmPanelHtml(clNum) {
    const sw = state.swarmByCl[clNum];
    if (!sw) return "";
    const findings = Array.isArray(sw.findings) ? sw.findings : [];
    const enrichedSw = Object.assign({}, sw, {
      findings: findings.map(f => {
        const loc = `${f.file || "/COMMIT_MSG"}:${f.line || 1}`;
        const locHtml = (f.file && f.file !== "/COMMIT_MSG")
          ? renderCodeSearchFileLinkHtml(clNum, f.file, loc, f.line || 1)
          : escapeHtml(loc);
        return Object.assign({}, f, { locHtml });
      }),
    });
    return window.AemuGerritReact.renderCommitSwarmPanelHtml(
      clNum,
      enrichedSw,
      getSwarmElapsedSec(sw),
      DEFAULT_SWARM_SPECIALISTS
    );
  }

  function getActiveRunningSwarmClNumbers() {
    return Object.keys(state.swarmByCl)
      .map(k => Number(k))
      .filter(num => state.swarmByCl[num] && state.swarmByCl[num].state === "running");
  }

  function jumpToActiveSwarmCl() {
    const runningCls = getActiveRunningSwarmClNumbers();
    if (runningCls.length > 0) {
      selectCl(runningCls[0], false, "next");
    }
  }

  function syncSwarmIndicatorsOnly(opts = {}) {
    const swarmObj = state.swarmByCl;
    const hasAnySwarm = hasAnyOwnProp(swarmObj);
    if (hasAnySwarm || state._hadAnySwarmBadges) {
      state._hadAnySwarmBadges = hasAnySwarm;
      const swarmSlots = document.querySelectorAll("[data-swarm-cl]");
      if (swarmSlots && swarmSlots.length > 0) {
        swarmSlots.forEach(slotEl => {
          const num = Number((slotEl.getAttribute && slotEl.getAttribute("data-swarm-cl")) || 0);
          if (num) slotEl.innerHTML = renderClSwarmBadgeHtml(num);
        });
      }
    }
    const runningCls = hasAnySwarm ? getActiveRunningSwarmClNumbers() : _PG_EMPTY_ARRAY;
    const qEl = document.getElementById("queueList");
    if (runningCls.length > 0 || state._hadSwarmingCards || (qEl && qEl._hadSwarmingCards)) {
      state._hadSwarmingCards = runningCls.length > 0;
      if (qEl) qEl._hadSwarmingCards = runningCls.length > 0;
      const cardEls = (qEl && typeof qEl.querySelectorAll === "function")
        ? qEl.querySelectorAll(".cl-card[data-cl-num]")
        : document.querySelectorAll(".cl-card[data-cl-num]");
      if (cardEls && cardEls.length > 0) {
        cardEls.forEach(cardEl => {
          const num = Number((cardEl.getAttribute && cardEl.getAttribute("data-cl-num")) || 0);
          const isRunning = Boolean(num && state.swarmByCl[num] && state.swarmByCl[num].state === "running");
          cardEl.classList.toggle("cl-card-swarming", isRunning);
        });
      }
    }
    if (!opts.skipPanelRender && (hasAnySwarm || state._hadAnySwarmPanels)) {
      state._hadAnySwarmPanels = hasAnySwarm;
      const activeCommits = Array.isArray(opts.commits) ? opts.commits : getActiveCommitsForDiff();
      activeCommits.forEach(c => {
        if (!c || !c.number) return;
        const panelEl = document.getElementById(`swarmPanel-${c.number}`);
        if (panelEl) {
          panelEl.innerHTML = renderCommitSwarmPanelHtml(c.number);
        }
      });
    }

    const btnSwarm = document.getElementById("btnRunSwarm");
    const swarmLabelEl = document.getElementById("btnRunSwarmLabel");
    const selCl = state.selectedClNumber;
    const selSwarm = (hasAnySwarm && selCl) ? state.swarmByCl[selCl] : null;
    if (btnSwarm && !state.queueLoading) {
      if (selSwarm && selSwarm.state === "running") {
        const done = Number(selSwarm.completed_specialists || 0);
        const total = Number(selSwarm.total_specialists || 8);
        const sec = getSwarmElapsedSec(selSwarm);
        const runSig = `run:${selCl}:${done}:${total}:${sec}`;
        if (btnSwarm._lastSwarmBtnSig !== runSig) {
          btnSwarm._lastSwarmBtnSig = runSig;
          btnSwarm.disabled = true;
          btnSwarm.classList.add("is-swarming");
          const runningHtml = `<span class="swarm-spinner"></span> Swarm Running on ag/${selCl} · ${done}/${total} (${sec}s)`;
          if (swarmLabelEl) {
            swarmLabelEl.innerHTML = runningHtml;
          } else {
            btnSwarm.innerHTML = runningHtml;
          }
        }
      } else {
        const isComp = Boolean(selSwarm && selSwarm.state === "completed");
        const idleSig = isComp ? `comp:${selCl}` : `idle:${selCl ? 1 : 0}`;
        if (btnSwarm._lastSwarmBtnSig !== idleSig) {
          btnSwarm._lastSwarmBtnSig = idleSig;
          btnSwarm.disabled = !selCl;
          btnSwarm.classList.remove("is-swarming");
          const idleText = isComp
            ? `↻ Re-Run Review Swarm on ag/${selCl}`
            : "★ Run Review Swarm (8 Specialists)";
          if (swarmLabelEl) {
            swarmLabelEl.textContent = idleText;
          } else {
            btnSwarm.textContent = idleText;
          }
        }
      }
    }
    if (!opts.skipHarnessMenuSync) {
      syncHarnessMenuButton(Array.isArray(opts.commits) ? opts.commits : undefined);
    }

    const pillEl = document.getElementById("activeSwarmsPill");
    if (pillEl) {
      const bgCls = runningCls.length > 0 ? runningCls.filter(num => num !== selCl) : _PG_EMPTY_ARRAY;
      if (bgCls.length > 0) {
        const firstNum = bgCls[0];
        const sw = state.swarmByCl[firstNum];
        const done = Number(sw.completed_specialists || 0);
        const total = Number(sw.total_specialists || 8);
        const sec = getSwarmElapsedSec(sw);
        pillEl.style.display = "inline-flex";
        pillEl.innerHTML = `<span class="swarm-spinner"></span> Swarm Active: ag/${firstNum} (${done}/${total} · ${sec}s) ↗`;
      } else if (pillEl.style.display !== "none") {
        pillEl.style.display = "none";
      }
    }

    const drawerDot = document.getElementById("drawerDot");
    const drawerStatus = document.getElementById("drawerStatus");
    if (state.queueLoading || state.diffLoadingCount > 0) {
      return;
    }
    if (runningCls.length > 0 && !state.votingInFlight) {
      const targetNum = runningCls.includes(selCl) ? selCl : runningCls[0];
      const sw = state.swarmByCl[targetNum];
      const done = Number(sw.completed_specialists || 0);
      const total = Number(sw.total_specialists || 8);
      const sec = getSwarmElapsedSec(sw);
      if (drawerDot) {
        drawerDot.className = "dot dot-purple-pulse";
      }
      if (drawerStatus) {
        drawerStatus.textContent = `Review Swarm active on ag/${targetNum} — ${done}/${total} specialists complete (${sec}s elapsed)…`;
      }
    } else if (drawerDot) {
      drawerDot.className = "dot dot-green";
    }
  }

  function mergePolledSwarmStatus(clNum, serverStatus, opts = {}) {
    if (!serverStatus) return;
    const prev = state.swarmByCl[clNum];
    if (prev && prev.optimisticPending && serverStatus.state === "completed") {
      const compMs = serverStatus.completed_at ? Date.parse(serverStatus.completed_at) : 0;
      if (!compMs || compMs < (prev.startedAtClientMs || 0) - 1500) {
        return;
      }
    }
    const wasRunning = Boolean(prev && prev.state === "running");
    const merged = Object.assign({}, serverStatus);
    if (merged.state === "running") {
      if (prev && prev.startedAtClientMs) {
        merged.startedAtClientMs = prev.startedAtClientMs;
      } else if (merged.started_at) {
        const parsedMs = Date.parse(merged.started_at);
        if (!Number.isNaN(parsedMs)) {
          merged.startedAtClientMs = parsedMs;
        }
      }
    }
    state.swarmByCl[clNum] = merged;
    if (wasRunning && merged.state === "completed" && !opts.silentToast) {
      const fCnt = Number(merged.total_findings || (merged.findings ? merged.findings.length : 0));
      const wall = merged.wall_clock_sec ? ` in ${Number(merged.wall_clock_sec).toFixed(1)}s` : "";
      const summary = fCnt === 0 ? "Clean (0 findings)" : `${fCnt} finding${fCnt === 1 ? "" : "s"}`;
      const drawerStatus = document.getElementById("drawerStatus");
      if (drawerStatus) {
        drawerStatus.textContent = `✓ Review Swarm completed on ag/${clNum}: ${summary}${wall}`;
      }
      showToast(`✓ Review Swarm finished on ag/${clNum}: ${summary}${wall}!`);
    }
  }

  async function pollActiveSwarmStatuses() {
    const runningCls = getActiveRunningSwarmClNumbers();
    if (runningCls.length === 0) return;
    try {
      const resp = await sidecarFetch(`/api/gerrit/swarm-status?cls=${runningCls.join(",")}`);
      if (!resp.ok) return;
      const data = await resp.json();
      const map = (data && data.statuses) || {};
      Object.keys(map).forEach(k => {
        mergePolledSwarmStatus(Number(k), map[k]);
      });
      syncSwarmIndicatorsOnly();
    } catch (_err) {
      // Ignore transient network hiccups during local status polling
    }
  }

  function ensureSwarmPollingTimer() {
    if (window._swarmTickInterval) return;
    let tickCount = 0;
    window._swarmTickInterval = setInterval(() => {
      const runningCls = getActiveRunningSwarmClNumbers();
      if (runningCls.length === 0) {
        clearInterval(window._swarmTickInterval);
        window._swarmTickInterval = null;
        syncSwarmIndicatorsOnly();
        return;
      }
      tickCount += 1;
      syncSwarmIndicatorsOnly();
      if (tickCount % 2 === 1) {
        pollActiveSwarmStatuses();
      }
    }, 1000);
  }

  async function postSwarmFindingsToGerrit(clNum, btnEl) {
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = `<span class="btn-spinner"></span> Posting to Gerrit…`;
    }
    try {
      const resp = await sidecarFetch("/api/gerrit/post-swarm-review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: clNum }),
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== "ok") {
        throw new Error(data.error || "Failed to post Review Swarm comments");
      }
      if (btnEl) {
        btnEl.textContent = "✓ Posted to Gerrit (notify: OWNER)";
      }
      showToast(`Posted Review Swarm findings to ag/${clNum} (notify: OWNER)!`);
    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = "Post to Gerrit (notify: OWNER)";
      }
      showToast("Could not post swarm comments: " + err);
    }
  }

  async function dispatchAgentPrompt(clNum, action, prompt) {
    if (!window.sidecar || !window.sidecar.agent) return false;
    try {
      if (window.sidecar.conversationId && typeof window.sidecar.agent.sendMessage === "function") {
        await window.sidecar.agent.sendMessage(prompt);
        return true;
      }
      if (typeof window.sidecar.agent.startConversation === "function") {
        await window.sidecar.agent.startConversation(prompt, `${action}: ag/${clNum}`);
        return true;
      }
    } catch (err) {
      console.warn("Agent dispatch warning:", err);
    }
    return false;
  }

  function initOptimisticSwarmRun(clNum) {
    state.swarmByCl[clNum] = {
      state: "running",
      phase: "queued",
      target_id: String(clNum),
      total_specialists: 8,
      completed_specialists: 0,
      startedAtClientMs: Date.now(),
      optimisticPending: true,
      elapsed_sec: 0,
      agents: DEFAULT_SWARM_SPECIALISTS.map(s => ({
        specialist: s.specialist,
        role: s.role,
        status: "running",
        duration_sec: null,
        finding_count: null,
      })),
    };
    syncSwarmIndicatorsOnly();
    ensureSwarmPollingTimer();
  }

  async function triggerHarness(action, btnEl) {
    if (!state.selectedClNumber) return;
    await triggerHarnessForCl(state.selectedClNumber, action, btnEl);
  }

  async function triggerHarnessForCl(clNum, action, btnEl) {
    const prompt = action === "review-swarm"
      ? `Run \`emu-dev-cli harness review-swarm --cl ${clNum}\` and execute the 8-specialist review swarm workflow for ag/${clNum}.`
      : `Run \`emu-dev-cli workflow address-review init --cl ${clNum}\` to address unresolved review comments on ag/${clNum}.`;

    if (action === "review-swarm") {
      const existing = state.swarmByCl[clNum];
      if (existing && existing.state === "running") {
        showToast(`Review Swarm is already running on ag/${clNum}!`);
        return;
      }
      initOptimisticSwarmRun(clNum);
    }

    const autoFixLabelEl = (btnEl && btnEl.id === "btnAutoFix")
      ? document.getElementById("btnAutoFixLabel")
      : null;
    const origBtnText = autoFixLabelEl
      ? autoFixLabelEl.textContent
      : (btnEl ? btnEl.textContent : "");
    if (action !== "review-swarm") {
      state.autoFixLaunchingCl = clNum;
      syncHarnessMenuButton();
    }
    if (btnEl && action !== "review-swarm") {
      btnEl.disabled = true;
      btnEl.classList.add("is-running-action");
      if (autoFixLabelEl) {
        autoFixLabelEl.innerHTML = `<span class="btn-spinner"></span> Launching Agent…`;
      } else {
        btnEl.innerHTML = `<span class="btn-spinner"></span> Launching Agent…`;
      }
    }

    document.getElementById("drawerStatus").textContent =
      action === "review-swarm"
        ? `Launching 8-Specialist Review Swarm on ag/${clNum}…`
        : `Running ${action} on ag/${clNum}…`;
    showToast(
      action === "review-swarm"
        ? `Launched 8-Specialist Review Swarm on ag/${clNum}!`
        : `Launched ${action} on ag/${clNum}!`
    );

    const agentPromise = dispatchAgentPrompt(clNum, action, prompt);
    try {
      const resp = await sidecarFetch("/api/gerrit/harness", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: clNum, action }),
      });
      const data = await resp.json();
      if (action === "review-swarm" && data && data.swarm_status) {
        const cur = state.swarmByCl[clNum];
        if (cur) cur.optimisticPending = false;
        mergePolledSwarmStatus(clNum, data.swarm_status, { silentToast: true });
      }
      await agentPromise;
      if (action !== "review-swarm") {
        document.getElementById("drawerStatus").textContent = `Completed ${action} on ag/${clNum} (status: ${data.status || "ok"})`;
      } else {
        syncSwarmIndicatorsOnly();
      }
    } catch (_err) {
      if (action !== "review-swarm") {
        document.getElementById("drawerStatus").textContent = `Prepared ${action} for ag/${clNum}`;
      }
    } finally {
      const cur = state.swarmByCl[clNum];
      if (cur) cur.optimisticPending = false;
      if (action !== "review-swarm") {
        state.autoFixLaunchingCl = null;
      }
      if (btnEl && action !== "review-swarm") {
        btnEl.classList.remove("is-running-action");
        btnEl.disabled = btnEl.id === "btnAutoFix" ? !state.selectedClNumber : false;
        if (autoFixLabelEl) {
          autoFixLabelEl.textContent = origBtnText;
        } else {
          btnEl.textContent = origBtnText;
        }
      }
      syncHarnessMenuButton();
    }
  }

  function getFallbackReviewersForCl(c) {
    if (!c) return [];
    if (Array.isArray(c.reviewers_detail)) return c.reviewers_detail;
    if (Array.isArray(c.reviewers)) return c.reviewers;
    const cr2Len = Array.isArray(c.human_cr2) ? c.human_cr2.length : 0;
    const cr1Len = Array.isArray(c.human_cr1) ? c.human_cr1.length : 0;
    const fallbackKey = `${cr2Len}:${cr1Len}:${c.owner_email || ""}:${state.viewer || ""}`;
    if (c._cachedFallbackReviewersKey === fallbackKey && Array.isArray(c._cachedFallbackReviewers)) {
      return c._cachedFallbackReviewers;
    }
    const out = [];
    const seen = new Set();
    const ownerLow = (c.owner_email || "").toLowerCase();
    const viewerLow = (state.viewer || "").toLowerCase();
    [...(c.human_cr2 || []), ...(c.human_cr1 || [])].forEach(v => {
      const em = (v.email || "").toLowerCase();
      if (!em || seen.has(em)) return;
      seen.add(em);
      const val = Number(v.value || 0);
      out.push({
        email: em,
        short: v.short || em.split("@")[0],
        name: v.name || v.short || em,
        full_name: v.name || v.short || em,
        state: "REVIEWER",
        is_bot: false,
        is_owner: em === ownerLow,
        is_viewer: em === viewerLow,
        removable: em !== ownerLow,
        cr_vote: val,
        votes: val ? [{ label: "Code-Review", short_label: "CR", value: val, formatted: val > 0 ? `+${val}` : String(val) }] : [],
      });
    });
    c._cachedFallbackReviewersKey = fallbackKey;
    c._cachedFallbackReviewers = out;
    return out;
  }

  const REVIEWER_ICON_SVG = `<svg class="rev-eye-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8s-2.4 4.5-6.5 4.5S1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/></svg>`;

  function getNeededApprovalBadgeHtml(c) {
    if (!c) return "";
    const reqCount = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
    const doneCount = Array.isArray(c.human_cr2)
      ? c.human_cr2.length
      : Number(c.human_cr2_count || 0);
    const remaining = Math.max(0, reqCount - doneCount);
    if (remaining >= 2) {
      return `<span class="slot slot-needed" title="Agent-authored/uploaded CL requires 2 human Code-Review +2 approvals">Needs two +2s</span>`;
    }
    if (remaining === 1) {
      return `<span class="slot slot-needed" title="Needs 1 more human Code-Review +2 approval">Needs +2</span>`;
    }
    return "";
  }

  function renderCardStatusRowItemsHtml(c) {
    const revs = getFallbackReviewersForCl(c).filter(p => !p.is_bot && !p.is_owner);
    const humanCcs = (c.ccs_detail || []).filter(p => !p.is_bot);
    const hasAs = Boolean(c.autosubmit_enabled);
    const hasPr = Boolean(c.presubmit_ready_enabled);
    const neededBadgeHtml = getNeededApprovalBadgeHtml(c);
    const revShortSet = new Set(revs.map(p => String(p.short || "").toLowerCase()));
    const viewerShort = String(state.viewer || "").split("@")[0].toLowerCase();
    const extraDonePills = (c.approval_slots || [])
      .filter(s => s.state === "done")
      .filter(s => {
        const txt = String(s.text || "").toLowerCase();
        if (txt.includes("you (+2") && revShortSet.has(viewerShort)) return false;
        for (const sh of revShortSet) {
          if (sh && txt.includes(sh)) return false;
        }
        return true;
      })
      .map(s => `<span class="slot slot-done">${escapeHtml(s.text)}</span>`);

    const statusPills = [];
    if (neededBadgeHtml) statusPills.push(neededBadgeHtml);
    statusPills.push(...extraDonePills);
    if (hasAs) {
      statusPills.push(`<span class="card-rev-pill cr-autosubmit" title="Autosubmit +1 enabled (${escapeHtml((c.autosubmit_voters || []).join(', ') || 'active')})">⇪ AS +1</span>`);
    }
    if (hasPr) {
      statusPills.push(`<span class="card-rev-pill" style="background:rgba(130,177,255,0.16);border-color:rgba(130,177,255,0.45);color:#d7e7ff;" title="Presubmit-Ready +1 active (${escapeHtml((c.presubmit_ready_voters || []).join(', ') || 'queued')})">▶ PR +1</span>`);
    }
    const revPills = revs.map(p => {
      const v = Number(p.cr_vote || 0);
      const cls = v >= 2 ? "cr-pos2" : (v === 1 ? "cr-pos1" : (v < 0 ? "cr-neg" : ""));
      const vStr = v > 0 ? `+${v}` : (v < 0 ? String(v) : "•");
      const attNote = p.in_attention_set ? " · Active turn in Attention Set" : "";
      const tip = `${p.full_name || p.name} <${p.email}> · Reviewer (CR ${v > 0 ? "+" + v : v})${attNote}`;
      return `<span class="card-rev-pill ${cls}" title="${escapeHtml(tip)}">${REVIEWER_ICON_SVG}<span>${escapeHtml(p.short)}</span> <strong>${escapeHtml(vStr)}</strong></span>`;
    });
    const ccPills = humanCcs.map(p => {
      const tip = `${p.full_name || p.name} <${p.email}> · CC`;
      return `<span class="card-rev-pill is-cc" title="${escapeHtml(tip)}">CC: ${escapeHtml(p.short)}</span>`;
    });
    return statusPills.concat(revPills, ccPills).join("");
  }

  const PG_AVATAR_PALETTE = [
    "#1a73e8",
    "#00897b",
    "#5e35b1",
    "#d81b60",
    "#f4511e",
    "#3949ab",
    "#039be5",
    "#43a047",
  ];

  function computePgAvatarColor(seed) {
    const str = String(seed || "gerrit").toLowerCase();
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
    }
    return PG_AVATAR_PALETTE[Math.abs(hash) % PG_AVATAR_PALETTE.length];
  }

  function computePgInitial(p) {
    if (p && p.is_bot) return "⚙";
    const src = String((p && (p.name || p.short || p.email)) || "?").trim();
    const first = Array.from(src)[0] || "?";
    return first.toUpperCase();
  }

  function renderPgAccountVoteCircleHtml(p, showVote) {
    if (!showVote) return "";
    const v = Number((p && p.cr_vote) || 0);
    if (v === 0) return "";
    const cls = v >= 2 ? "v-pos2" : (v === 1 ? "v-pos1" : "v-neg");
    const fmt = v > 0 ? `+${v}` : String(v);
    return `<span class="pg-account-vote-circle ${cls}" title="Code-Review ${escapeHtml(fmt)}">${escapeHtml(fmt)}</span>`;
  }

  function renderPgAccountChipActionsHtml(clNum, p, readOnly, inReplyDialog = false) {
    if (readOnly) return "";
    const acctKey = p.email || (p.account_id != null ? String(p.account_id) : p.short);
    const nextState = p.state === "CC" ? "REVIEWER" : "CC";
    const moveTitle = p.state === "CC" ? "Move CC → Reviewer (notify: OWNER)" : "Move Reviewer → CC (notify: OWNER)";
    const sentMeta = p.email ? state.sentChatDms[`${clNum}:${p.email.toLowerCase()}`] : null;
    const chatTitle = sentMeta
      ? `Chat DM sent at ${sentMeta.sentAtStr} — click to message ${p.email} again`
      : `Send 1:1 Google Chat DM to ${p.email} about ag/${clNum}`;
    const chatBtn = (!inReplyDialog && isDmEligibleParticipant(p))
      ? `<button type="button" class="pg-chip-act ${sentMeta ? "chat-sent" : ""}" data-email="${escapeHtml(p.email)}" title="${escapeHtml(chatTitle)}" onclick="event.stopPropagation(); openChatDmComposer(${clNum}, this.dataset.email)">${sentMeta ? "💬✓" : "💬"}</button>`
      : "";
    const moveBtn = (!p.is_owner && acctKey)
      ? `<button type="button" class="pg-chip-act" title="${escapeHtml(moveTitle)}" onclick="event.stopPropagation(); moveParticipantState(${clNum}, '${escapeHtml(acctKey)}', '${nextState}', '${escapeHtml(p.short)}', this)">⇄</button>`
      : "";
    const removeBtn = (p.removable !== false && !p.is_owner && acctKey)
      ? `<button type="button" class="pg-chip-act remove" title="Remove ${escapeHtml(p.short)} from CL (notify: OWNER)" onclick="event.stopPropagation(); removeParticipantFromCl(${clNum}, '${escapeHtml(acctKey)}', '${escapeHtml(p.short)}', this)">×</button>`
      : "";
    return `${chatBtn}${moveBtn}${removeBtn}`;
  }

  function renderPgAccountChipHtml(clNum, p, opts = {}) {
    const disp = p.name || p.short || p.email || "unknown";
    const voteSummary = Array.isArray(p.votes) && p.votes.length > 0
      ? p.votes.map(v => `${v.label}: ${v.formatted}`).join(", ")
      : "No votes";
    const attNote = p.in_attention_set ? " · Active turn in Attention Set" : "";
    const roleLabel = p.state || (p.is_owner ? "OWNER" : "REVIEWER");
    const tip = `${p.full_name || disp}${p.email ? " <" + p.email + ">" : ""} · ${roleLabel} · ${voteSummary}${attNote}`;
    const avatarBg = p.is_bot ? "#5f6368" : computePgAvatarColor(p.email || disp);
    const initial = computePgInitial(p);
    const attnHtml = p.in_attention_set
      ? `<span class="pg-account-attn-dot" title="Active turn in Attention Set">▸</span>`
      : "";
    const clsParts = ["pg-account-chip", "participant-chip"];
    if (p.is_viewer) clsParts.push("is-viewer");
    if (p.is_bot) clsParts.push("is-bot");
    if (p.in_attention_set) clsParts.push("has-attention");
    return `<span class="${clsParts.join(" ")}" title="${escapeHtml(tip)}">
      ${attnHtml}<span class="pg-account-avatar" style="background:${avatarBg};">${escapeHtml(initial)}</span><span class="pg-account-name">${escapeHtml(disp)}</span>${renderPgAccountVoteCircleHtml(p, opts.showVote !== false)}${renderPgAccountChipActionsHtml(clNum, p, Boolean(opts.readOnly), Boolean(opts.inReplyDialog))}
    </span>`;
  }

  function renderParticipantChipHtml(clNum, p) {
    return renderPgAccountChipHtml(clNum, p, { showVote: p.state === "REVIEWER" });
  }

  function renderReviewerSuggestMenuHtml(comp, buildOnSelectJs) {
    if (comp.loading && (!comp.suggestions || comp.suggestions.length === 0)) {
      return `<div style="padding:6px 8px;color:var(--g-muted);font-size:11px;"><span class="btn-spinner"></span> Searching Gerrit accounts…</div>`;
    }
    const items = comp.suggestions || [];
    if (items.length === 0) {
      return `<div style="padding:6px 8px;color:var(--g-muted);font-size:11px;">Press <strong>Enter</strong> to add typed LDAP or email</div>`;
    }
    return items.map((s, idx) => {
      const activeCls = idx === comp.activeIdx ? "active" : "";
      const icon = s.is_group ? "👥" : "👤";
      const rightTxt = s.email && s.email !== s.name ? s.email : "";
      const onSelectJs = buildOnSelectJs(escapeHtml(s.value));
      return `<div class="reviewer-suggest-item ${activeCls}" onmousedown="event.preventDefault(); ${onSelectJs}">
        <span>${icon} <strong>${escapeHtml(s.name || s.short)}</strong></span>
        <span style="color:var(--g-muted);font-size:10.5px;">${escapeHtml(rightTxt)}</span>
      </div>`;
    }).join("");
  }

  function renderSuggestMenuInnerHtml(clNum, targetState, comp) {
    return renderReviewerSuggestMenuHtml(
      comp,
      escapedVal => `submitReviewerAdd(${clNum}, '${targetState}', '${escapedVal}')`
    );
  }

  function renderReviewerComposerHtml(clNum, targetState, context = "") {
    const comp = state.activeReviewerComposer;
    const ctx = context || "";
    const isOpen = comp && Number(comp.clNum) === Number(clNum) && comp.targetState === targetState && (comp.context || "") === ctx;
    const btnLabel = targetState === "CC" ? "+ Add CC" : "+ Add Reviewer";
    const ctxArg = ctx ? `, '${escapeHtml(ctx)}'` : "";
    if (!isOpen) {
      return `<button class="add-participant-btn" onclick="event.stopPropagation(); openReviewerComposer(${clNum}, '${targetState}'${ctxArg})">${btnLabel}</button>`;
    }
    const ph = targetState === "CC" ? "Add CC (LDAP, name, email)…" : "Add reviewer (LDAP, name, email)…";
    const btnHtml = comp.submitting
      ? `<span class="btn-spinner"></span> Adding…`
      : (targetState === "CC" ? "Add CC" : "Add");
    const inputId = ctx ? `reviewerInput-${ctx}-${clNum}-${targetState}` : `reviewerInput-${clNum}-${targetState}`;
    const menuId = ctx ? `reviewerSuggestMenu-${ctx}-${clNum}-${targetState}` : `reviewerSuggestMenu-${clNum}-${targetState}`;
    return `<div class="reviewer-add-box" onclick="event.stopPropagation()">
      <input
        id="${inputId}"
        class="reviewer-add-input"
        type="text"
        value="${escapeHtml(comp.query || "")}"
        placeholder="${ph}"
        ${comp.submitting ? "disabled" : ""}
        oninput="onReviewerInputChanged(${clNum}, '${targetState}', this.value)"
        onkeydown="onReviewerInputKeydown(event, ${clNum}, '${targetState}')"
      />
      <button class="nudge-btn" ${comp.submitting ? "disabled" : ""} onclick="submitReviewerAdd(${clNum}, '${targetState}', null, this)">${btnHtml}</button>
      <button class="nudge-btn" ${comp.submitting ? "disabled" : ""} onclick="closeReviewerComposer()" title="Cancel (Esc)">✕</button>
      <div id="${menuId}" class="reviewer-suggest-menu">
        ${renderSuggestMenuInnerHtml(clNum, targetState, comp)}
      </div>
    </div>`;
  }

  function renderPgReplyParticipantsInnerHtml(c) {
    if (!c) return "";
    const allRevs = (typeof getFallbackReviewersForCl === "function")
      ? getFallbackReviewersForCl(c)
      : (Array.isArray(c.reviewers_detail) ? c.reviewers_detail : (Array.isArray(c.reviewers) ? c.reviewers : []));
    const displayRevs = allRevs.filter(p => !p.is_owner || Number(p.cr_vote || 0) !== 0);
    const ccs = Array.isArray(c.ccs_detail) ? c.ccs_detail : (Array.isArray(c.ccs) ? c.ccs : []);
    const revChipsHtml = displayRevs.map(p => renderPgAccountChipHtml(c.number, p, { showVote: true, inReplyDialog: true })).join("");
    const ccChipsHtml = ccs.map(p => renderPgAccountChipHtml(c.number, p, { showVote: false, inReplyDialog: true })).join("");
    return `<div class="pg-reply-meta-row">
      <span class="pg-reply-meta-key">Reviewers</span>
      <div class="pg-reply-meta-val">
        ${revChipsHtml}
        ${renderReviewerComposerHtml(c.number, "REVIEWER", "reply")}
      </div>
    </div>
    <div class="pg-reply-meta-row">
      <span class="pg-reply-meta-key">CC</span>
      <div class="pg-reply-meta-val">
        ${ccChipsHtml}
        ${renderReviewerComposerHtml(c.number, "CC", "reply")}
      </div>
    </div>`;
  }

  function syncPgReplyParticipantsDom(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const container = document.getElementById(`pgReplyParticipantsSection-${num}`);
    const clObj = state.changes.find(x => Number(x.number) === num);
    if (container && clObj) {
      container.innerHTML = renderPgReplyParticipantsInnerHtml(clObj);
    }
  }

  const PRESUBMIT_KIND_LABELS = {
    build: "BUILD",
    test: "TEST",
    preflight: "PREFLIGHT",
    test_module: "MODULE",
  };
  const PRESUBMIT_POLL_INTERVAL_MS = 8000;

  function renderPresubmitStatusPillHtml(t) {
    return window.AemuGerritReact.renderPresubmitStatusPillHtml(t);
  }

  function renderSinglePresubmitTargetRowHtml(t, fallbackChecksUrl) {
    return window.AemuGerritReact.renderSinglePresubmitTargetRowHtml(t, fallbackChecksUrl);
  }

  function filterPresubmitTargetsList(targets, filterId) {
    if (!filterId || filterId === "all") return targets;
    if (filterId === "failed") return targets.filter(t => t.status === "failed" || t.status === "warning");
    if (filterId === "running") return targets.filter(t => t.status === "running" || t.status === "queued");
    if (filterId === "passed") return targets.filter(t => t.status === "passed");
    return targets;
  }

  function renderPresubmitDrawerBodyHtml(c, entry, filterId, checksUrl) {
    const num = Number(c.number);
    const ps = Number((entry && entry.data && entry.data.patch_set) || c.patch_set || 1);
    if (!entry || (entry.loading && !entry.data)) {
      return `<div style="padding:14px 12px;color:var(--g-muted);font-size:11.5px;display:flex;align-items:center;gap:8px;">
        <span class="btn-spinner"></span>
        <span>Querying TreeHugger presubmit build &amp; test targets for <strong>ag/${num}</strong> (PS${ps})…</span>
      </div>`;
    }
    if (entry.error && !entry.data) {
      const checksLink = checksUrl
        ? `<a class="ag-link" href="${escapeHtml(checksUrl)}" target="_blank" rel="noopener noreferrer">Open Gerrit Checks Tab ↗</a>`
        : "";
      return `<div style="padding:12px;font-size:11.5px;color:#f4b4af;display:flex;flex-direction:column;gap:8px;">
        <div>${escapeHtml(entry.error)}</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <button class="nudge-btn" onclick="event.stopPropagation(); fetchPresubmitTargetsForCl(${num}, { forceRefresh: true })">↻ Retry</button>
          ${checksLink}
        </div>
      </div>`;
    }
    const targets = (entry.data && entry.data.targets) || [];
    if (targets.length === 0) {
      const triggerBtn = !c.viewer_presubmit_ready
        ? `<button class="nudge-btn" onclick="event.stopPropagation(); toggleStagedPr(true)">☑ Stage PR +1</button>`
        : `<span style="color:#f0cf95;font-weight:600;">⏳ Waiting for TreeHugger workplan to allocate targets…</span>`;
      return `<div style="padding:12px;font-size:11.5px;color:var(--g-muted);display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
        <span>No TreeHugger build/test targets recorded yet for <strong>ag/${num}</strong> (PS${ps}).</span>
        <div style="display:inline-flex;align-items:center;gap:8px;">${triggerBtn}</div>
      </div>`;
    }
    const filtered = filterPresubmitTargetsList(targets, filterId);
    if (filtered.length === 0) {
      return `<div style="padding:12px;font-size:11.5px;color:var(--g-muted);">
        No targets match the <strong>${escapeHtml(filterId)}</strong> filter.
        <button class="nudge-btn" style="margin-left:6px;" onclick="event.stopPropagation(); setPresubmitTargetFilter(${num}, 'all')">Show All (${targets.length})</button>
      </div>`;
    }
    return `<div class="ps-targets-list">${filtered.map(t => renderSinglePresubmitTargetRowHtml(t, checksUrl)).join("")}</div>`;
  }

  function renderPresubmitTargetsDrawerHtml(c) {
    return "";
  }

  function renderPgChecksTabButtonHtml(c, activeTabOverride) {
    const num = Number(c && c.number ? c.number : 0);
    const activeTab = activeTabOverride || (state.activeChangeTabByCl && state.activeChangeTabByCl[num]) || "files";
    const entry = state.presubmitTargetsByCl[num];
    const pv = getClPresubmitDisplay(c);
    const tCount = (entry && entry.data && Array.isArray(entry.data.targets)) ? entry.data.targets.length : -1;
    const isLoading = Boolean(entry && entry.loading);
    const pvSt = pv.status || "";
    const pvTxt = pv.text || "";
    const pvF = pv.failedCount || 0;
    const pvR = pv.runningCount || 0;
    const pvP = pv.passedCount || 0;
    const pvT = pv.totalCount || 0;
    if (c && typeof c._cachedChecksTabBtnHtml === "string") {
      const cbSub = c._checksTabBtnSub;
      if (
        cbSub &&
        cbSub.num === num &&
        cbSub.activeTab === activeTab &&
        cbSub.loading === isLoading &&
        cbSub.tCount === tCount &&
        cbSub.pvSt === pvSt &&
        cbSub.pvTxt === pvTxt &&
        cbSub.pvF === pvF &&
        cbSub.pvR === pvR &&
        cbSub.pvP === pvP &&
        cbSub.pvT === pvT
      ) {
        return c._cachedChecksTabBtnHtml;
      }
    }
    const cacheKey = `${num}|${activeTab}|${isLoading ? 1 : 0}|${tCount}|${pvSt}|${pvTxt}|${pvF}|${pvR}|${pvP}|${pvT}`;
    if (c && c._cachedChecksTabBtnKey === cacheKey && typeof c._cachedChecksTabBtnHtml === "string") {
      c._checksTabBtnSub = { num, activeTab, loading: isLoading, tCount, pvSt, pvTxt, pvF, pvR, pvP, pvT };
      return c._cachedChecksTabBtnHtml;
    }
    const html = window.AemuGerritReact.renderPgChecksTabButtonHtml(c, activeTab, entry, pv);
    if (c) {
      c._cachedChecksTabBtnKey = cacheKey;
      c._cachedChecksTabBtnHtml = html;
      c._checksTabBtnSub = { num, activeTab, loading: isLoading, tCount, pvSt, pvTxt, pvF, pvR, pvP, pvT };
    }
    return html;
  }

  function renderPgChecksControlsHtml(c) {
    const num = Number(c && c.number ? c.number : 0);
    const entry = state.presubmitTargetsByCl[num];
    const pv = getClPresubmitDisplay(c);
    const tCount = (entry && entry.data && Array.isArray(entry.data.targets)) ? entry.data.targets.length : -1;
    const isLoading = Boolean(entry && entry.loading);
    const pvSt = pv.status || "";
    const pvUrl = pv.checksUrl || "";
    const vPr = Boolean(c && c.viewer_presubmit_ready);
    if (c && typeof c._cachedChecksControlsHtml === "string") {
      const ccSub = c._checksCtrlsSub;
      if (
        ccSub &&
        ccSub.num === num &&
        ccSub.loading === isLoading &&
        ccSub.tCount === tCount &&
        ccSub.pvSt === pvSt &&
        ccSub.pvUrl === pvUrl &&
        ccSub.vPr === vPr
      ) {
        return c._cachedChecksControlsHtml;
      }
    }
    const cacheKey = `${num}|${isLoading ? 1 : 0}|${tCount}|${pvSt}|${pvUrl}|${vPr ? 1 : 0}`;
    if (c && c._cachedChecksControlsKey === cacheKey && typeof c._cachedChecksControlsHtml === "string") {
      c._checksCtrlsSub = { num, loading: isLoading, tCount, pvSt, pvUrl, vPr };
      return c._cachedChecksControlsHtml;
    }
    const html = window.AemuGerritReact.renderPgChecksControlsHtml(c, entry, pv);
    if (c) {
      c._cachedChecksControlsKey = cacheKey;
      c._cachedChecksControlsHtml = html;
      c._checksCtrlsSub = { num, loading: isLoading, tCount, pvSt, pvUrl, vPr };
    }
    return html;
  }

  function renderPgCheckDetailCardHtml(c, t, pv) {
    const num = Number(c.number);
    const isLogOpen = Boolean(state.checkLogOpenByCl && state.checkLogOpenByCl[num]);
    const logSearchInput = (state.checkLogSearchByCl && state.checkLogSearchByCl[num]) || "";
    const allLines = generatePgCheckRunLogLines(c, t, pv);
    return window.AemuGerritReact.renderPgCheckDetailCardHtml(c, t, pv, isLogOpen, logSearchInput, allLines);
  }

  function renderPgCheckRunLogDrawerHtml(c, t, pv, isLogOpen) {
    const num = Number(c.number);
    const logSearchInput = (state.checkLogSearchByCl && state.checkLogSearchByCl[num]) || "";
    const allLines = generatePgCheckRunLogLines(c, t, pv);
    return window.AemuGerritReact.renderPgCheckRunLogDrawerHtml(c, t, pv, isLogOpen, logSearchInput, allLines);
  }

  function generatePgCheckRunLogLines(c, t, pv) {
    const num = Number(c.number);
    const ps = Number(c.patch_set || 1);
    const kindLabel = PRESUBMIT_KIND_LABELS[t.kind] || "TARGET";
    const rawStatus = t.raw_status || t.status || "UNKNOWN";
    const artId = t.artifact_id || `inv-${num}-${ps}`;
    const worknode = t.worknode_id || `worknode-${num}`;
    const branch = t.branch || c.branch || "goog/studio-main";
    const buildTarget = t.build_target || "generic_x86_64";
    const dur = t.duration_display || "0s";
    const mainUrl = t.url || pv.checksUrl || `https://sponge.corp.google.com/${artId}`;
    const secUrl = t.secondary_url || "";

    const lines = [
      { type: "meta", text: `=== Android Build / TreeHugger Presubmit Execution ===` },
      { type: "meta", text: `Target:       ${t.name}` },
      { type: "meta", text: `Kind:         ${kindLabel}` },
      { type: "meta", text: `Change:       ag/${num} (Patchset ${ps})` },
      { type: "meta", text: `Branch:       ${branch}` },
      { type: "meta", text: `Build Target: ${buildTarget}` },
      { type: "meta", text: `Worker Node:  ${worknode}` },
      { type: "meta", text: `Invocation:   ${artId}` },
      { type: "meta", text: `Status:       ${rawStatus} (${t.blocking_level || "BLOCKING"})` },
      { type: "meta", text: `Duration:     ${dur}` },
      { type: "meta", text: `======================================================` },
      { type: "info", text: `[00:00:01] Worker node initialized in region us-central1` },
      { type: "info", text: `[00:00:03] Syncing repository ${c.project || "platform/hardware/google/aemu"} at revision ${ps}...` },
      { type: "info", text: `[00:00:07] Resolved target dependencies and manifest configuration` },
    ];

    if (t.cached) {
      lines.push({ type: "info", text: `[00:00:10] Querying Android Build remote action cache...` });
      lines.push({ type: "pass", text: `[00:00:12] [SUCCESS] Remote cache HIT for target ${t.name} (action hash: ${artId})` });
      lines.push({ type: "pass", text: `[00:00:13] [SUCCESS] Target result verified successfully from cache in ${dur}` });
    } else if (t.status === "passed") {
      lines.push({ type: "info", text: `[00:00:12] Spawning hermetic sandbox runner...` });
      lines.push({ type: "info", text: `[00:00:25] Compiling and running verification suites for ${t.name}...` });
      lines.push({ type: "pass", text: `[00:00:58] [SUCCESS] All verification tests passed (0 failures, 0 errors).` });
      lines.push({ type: "pass", text: `[00:01:00] [SUCCESS] Target ${t.name} completed with status PASS (${dur}).` });
    } else if (t.status === "failed" || t.status === "warning") {
      lines.push({ type: "info", text: `[00:00:12] Spawning hermetic sandbox runner...` });
      lines.push({ type: "info", text: `[00:00:28] Executing test runner for ${t.name}...` });
      const errSub = t.subtitle || "Verification target exited with non-zero status.";
      lines.push({ type: "err", text: `[00:01:15] [ERROR] Failure detected: ${errSub}` });
      lines.push({ type: "err", text: `[00:01:16] [ERROR] Process terminated with exit code 1 (FAILED)` });
      if (secUrl) {
        lines.push({ type: "err", text: `[00:01:17] [ERROR] Detailed build/test log URL: ${secUrl}` });
      }
      lines.push({ type: "err", text: `[00:01:18] [ERROR] Sponge invocation link: ${mainUrl}` });
      lines.push({ type: "warn", text: `[00:01:19] [WARN] Target marked as ${t.blocking_level || "BLOCKING"}` });
    } else if (t.status === "running" || t.status === "queued") {
      lines.push({ type: "info", text: `[00:00:12] Job scheduled on TreeHugger build cluster` });
      lines.push({ type: "running", text: `[00:00:20] ◷ [IN_PROGRESS] Target currently executing on Android Build worker...` });
    } else {
      lines.push({ type: "info", text: `[00:00:01] Target status: ${rawStatus}` });
    }

    const rerunLogs = (state.rerunLogsByCl && state.rerunLogsByCl[num] && state.rerunLogsByCl[num][t.name]) || [];
    if (rerunLogs.length > 0) {
      lines.push({ type: "meta", text: `--- Rerun Session ---` });
      lines.push(...rerunLogs);
    }

    return lines;
  }

  function renderPgChecksViewHtml(c) {
    const num = Number(c.number);
    const entry = state.presubmitTargetsByCl[num] || null;
    const pv = getClPresubmitDisplay(c);
    const filterId = state.presubmitTargetFilterByCl[num] || "all";
    const kindFilter = (state.checksKindFilterByCl && state.checksKindFilterByCl[num]) || "all";
    const searchQueryInput = (state.checksSearchQueryByCl && state.checksSearchQueryByCl[num]) || "";
    const selectedRunName = (state.selectedCheckRunByCl && state.selectedCheckRunByCl[num]) || "";
    const isLogOpen = Boolean(state.checkLogOpenByCl && state.checkLogOpenByCl[num]);
    const logSearchInput = (state.checkLogSearchByCl && state.checkLogSearchByCl[num]) || "";
    const liveSummaryText = formatLiveTargetsSummaryText(c, pv);
    const tList = (entry && entry.data && Array.isArray(entry.data.targets)) ? entry.data.targets : null;
    const rerunMap = state.rerunLogsByCl && state.rerunLogsByCl[num];
    const psNum = c.patch_set || 1;
    const vPr = Boolean(c.viewer_presubmit_ready);
    const isLoading = Boolean(entry && entry.loading);
    const errStr = (entry && entry.error) || "";
    const pvSt = pv.status || "";
    const pvTxt = pv.text || "";
    if (!tList && !rerunMap && typeof c._cachedChecksViewHtml === "string") {
      const cvSub = c._checksViewSub;
      if (
        cvSub &&
        cvSub.num === num &&
        cvSub.ps === psNum &&
        cvSub.vPr === vPr &&
        cvSub.loading === isLoading &&
        cvSub.err === errStr &&
        cvSub.pvSt === pvSt &&
        cvSub.pvTxt === pvTxt &&
        cvSub.filterId === filterId &&
        cvSub.kindFilter === kindFilter &&
        cvSub.search === searchQueryInput &&
        cvSub.selRun === selectedRunName &&
        cvSub.logOpen === isLogOpen &&
        cvSub.logSearch === logSearchInput &&
        cvSub.liveTxt === liveSummaryText
      ) {
        return c._cachedChecksViewHtml;
      }
    }
    const tSig = tList ? `${tList.length}:${tList.map(t => `${t.name}:${t.status}`).join(",")}` : "none";
    const rerunSig = rerunMap ? Object.keys(rerunMap).map(k => `${k}:${(rerunMap[k] || []).length}`).join(",") : "";
    const cacheKey = `${num}|${psNum}|${vPr ? 1 : 0}|${isLoading ? 1 : 0}|${errStr}|${tSig}|${pvSt}|${pvTxt}|${filterId}|${kindFilter}|${searchQueryInput}|${selectedRunName}|${isLogOpen ? 1 : 0}|${logSearchInput}|${liveSummaryText}|${rerunSig}`;
    if (c._cachedChecksViewKey === cacheKey && typeof c._cachedChecksViewHtml === "string") {
      if (!tList && !rerunMap) {
        c._checksViewSub = { num, ps: psNum, vPr, loading: isLoading, err: errStr, pvSt, pvTxt, filterId, kindFilter, search: searchQueryInput, selRun: selectedRunName, logOpen: isLogOpen, logSearch: logSearchInput, liveTxt: liveSummaryText };
      }
      return c._cachedChecksViewHtml;
    }
    const html = window.AemuGerritReact.renderPgChecksViewComponentHtml(
      c,
      entry,
      pv,
      filterId,
      kindFilter,
      searchQueryInput,
      selectedRunName,
      isLogOpen,
      logSearchInput,
      liveSummaryText,
      (t) => generatePgCheckRunLogLines(c, t, pv)
    );
    c._cachedChecksViewKey = cacheKey;
    c._cachedChecksViewHtml = html;
    if (!tList && !rerunMap) {
      c._checksViewSub = { num, ps: psNum, vPr, loading: isLoading, err: errStr, pvSt, pvTxt, filterId, kindFilter, search: searchQueryInput, selRun: selectedRunName, logOpen: isLogOpen, logSearch: logSearchInput, liveTxt: liveSummaryText };
    }
    return html;
  }

  function selectPgChangeTab(clNum, tabName) {
    const num = Number(clNum || state.selectedClNumber || (state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const tab = (tabName === "checks") ? "checks" : (tabName === "comments" ? "comments" : "files");
    state.activeChangeTabByCl = state.activeChangeTabByCl || {};
    state.activeChangeTabByCl[num] = tab;

    if (tab === "checks") {
      const entry = state.presubmitTargetsByCl[num];
      if (!entry || (!entry.data && !entry.loading)) {
        fetchPresubmitTargetsForCl(num);
      }
    }

    const filesTab = document.getElementById(`pgTabFiles-${num}`);
    const commentsTab = document.getElementById(`pgTabComments-${num}`);
    const checksTab = document.getElementById(`pgTabChecks-${num}`);
    if (filesTab) {
      filesTab.classList.toggle("active", tab === "files");
      filesTab.classList.toggle("pg-tab-link", tab !== "files");
    }
    if (commentsTab) {
      commentsTab.classList.toggle("active", tab === "comments");
      commentsTab.classList.toggle("pg-tab-link", tab !== "comments");
    }
    if (checksTab) {
      checksTab.classList.toggle("active", tab === "checks");
      checksTab.classList.toggle("pg-tab-link", tab !== "checks");
    }

    const filesSub = document.getElementById(`pgFilesTabSubControls-${num}`);
    if (filesSub) {
      filesSub.style.display = tab === "files" ? "inline-flex" : "none";
    }

    const filesPane = document.getElementById(`pgFilesTabPane-${num}`);
    const commentsPane = document.getElementById(`pgCommentsTabPane-${num}`);
    const checksPane = document.getElementById(`pgChecksTabPane-${num}`);
    if (filesPane) {
      filesPane.classList.toggle("is-active", tab === "files");
      filesPane.style.display = tab === "files" ? "block" : "none";
    }
    if (commentsPane) {
      commentsPane.classList.toggle("is-active", tab === "comments");
      commentsPane.style.display = tab === "comments" ? "block" : "none";
      if (tab === "comments") {
        const c = state.changes.find(x => Number(x.number) === num);
        const bundle = state.diffsByCl && state.diffsByCl[num];
        if (c && typeof renderPgCommentsViewHtml === "function") {
          commentsPane.innerHTML = renderPgCommentsViewHtml(c, bundle);
        }
      }
    }
    if (checksPane) {
      checksPane.classList.toggle("is-active", tab === "checks");
      checksPane.style.display = tab === "checks" ? "block" : "none";
      if (tab === "checks") {
        const c = state.changes.find(x => Number(x.number) === num);
        if (c) checksPane.innerHTML = renderPgChecksViewHtml(c);
      }
    }

    const filesControls = document.getElementById(`pgFilesControls-${num}`);
    const checksControls = document.getElementById(`pgChecksControls-${num}`);
    if (filesControls) filesControls.style.display = tab === "files" ? "inline-flex" : "none";
    if (checksControls) checksControls.style.display = tab === "checks" ? "inline-flex" : "none";

    const tabsBar = document.getElementById(`pgTabsBar-${num}`);
    if (tabsBar && typeof tabsBar.scrollIntoView === "function") {
      tabsBar.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function togglePgChangeTab(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const curTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[num]) || "files";
    const nextTab = curTab === "files" ? "comments" : (curTab === "comments" ? "checks" : "files");
    selectPgChangeTab(num, nextTab);
  }

  function selectPgCheckRunByIndex(clNum, idx) {
    const num = Number(clNum);
    const entry = state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const filterId = state.presubmitTargetFilterByCl[num] || "all";
    const kindFilter = (state.checksKindFilterByCl && state.checksKindFilterByCl[num]) || "all";
    const searchQuery = String((state.checksSearchQueryByCl && state.checksSearchQueryByCl[num]) || "").trim().toLowerCase();
    let filtered = filterPresubmitTargetsList(targets, filterId);
    if (kindFilter !== "all") {
      filtered = filtered.filter(t => {
        const k = String(t.kind || "").toLowerCase();
        if (kindFilter === "build") return k.includes("build");
        if (kindFilter === "test") return k.includes("test");
        if (kindFilter === "preflight") return k.includes("preflight") || k.includes("check") || k.includes("lint");
        return true;
      });
    }
    if (searchQuery) {
      filtered = filtered.filter(t => {
        const name = String(t.name || "").toLowerCase();
        const sub = String(t.subtitle || "").toLowerCase();
        const kind = String(t.kind || "").toLowerCase();
        const art = String(t.artifact_id || "").toLowerCase();
        return name.includes(searchQuery) || sub.includes(searchQuery) || kind.includes(searchQuery) || art.includes(searchQuery);
      });
    }
    const t = filtered[idx];
    if (t) {
      state.selectedCheckRunByCl = state.selectedCheckRunByCl || {};
      state.selectedCheckRunByCl[num] = t.name;
    }
    rerenderPgChecksViewDom(num);
  }

  function navigatePgCheckRun(clNum, dir) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    if (targets.length === 0) return;

    const filterId = state.presubmitTargetFilterByCl[num] || "all";
    const kindFilter = (state.checksKindFilterByCl && state.checksKindFilterByCl[num]) || "all";
    const searchQuery = String((state.checksSearchQueryByCl && state.checksSearchQueryByCl[num]) || "").trim().toLowerCase();

    let filtered = filterPresubmitTargetsList(targets, filterId);
    if (kindFilter !== "all") {
      filtered = filtered.filter(t => {
        const k = String(t.kind || "").toLowerCase();
        if (kindFilter === "build") return k.includes("build");
        if (kindFilter === "test") return k.includes("test");
        if (kindFilter === "preflight") return k.includes("preflight") || k.includes("check") || k.includes("lint");
        return true;
      });
    }
    if (searchQuery) {
      filtered = filtered.filter(t => {
        const name = String(t.name || "").toLowerCase();
        const sub = String(t.subtitle || "").toLowerCase();
        const kind = String(t.kind || "").toLowerCase();
        const art = String(t.artifact_id || "").toLowerCase();
        return name.includes(searchQuery) || sub.includes(searchQuery) || kind.includes(searchQuery) || art.includes(searchQuery);
      });
    }
    if (filtered.length === 0) return;

    const selectedName = state.selectedCheckRunByCl && state.selectedCheckRunByCl[num];
    let curIdx = filtered.findIndex(t => t.name === selectedName);
    if (curIdx === -1) curIdx = 0;

    let targetIdx = curIdx;
    if (dir === "next") {
      targetIdx = Math.min(filtered.length - 1, curIdx + 1);
    } else if (dir === "prev") {
      targetIdx = Math.max(0, curIdx - 1);
    }
    selectPgCheckRunByIndex(num, targetIdx);
  }

  function setPgChecksCategoryFilter(clNum, filterId) {
    setPresubmitTargetFilter(clNum, filterId);
  }

  function setPgChecksKindFilter(clNum, kindId) {
    const num = Number(clNum);
    state.checksKindFilterByCl = state.checksKindFilterByCl || {};
    state.checksKindFilterByCl[num] = kindId || "all";
    rerenderPgChecksViewDom(num);
  }

  function togglePgCheckLogDrawer(clNum, targetName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    state.checkLogOpenByCl = state.checkLogOpenByCl || {};
    state.checkLogOpenByCl[num] = !state.checkLogOpenByCl[num];
    if (targetName) {
      state.selectedCheckRunByCl = state.selectedCheckRunByCl || {};
      state.selectedCheckRunByCl[num] = targetName;
    }
    rerenderPgChecksViewDom(num);
  }

  function onPgCheckLogSearchInput(clNum, val) {
    const num = Number(clNum);
    state.checkLogSearchByCl = state.checkLogSearchByCl || {};
    state.checkLogSearchByCl[num] = val;
    rerenderPgChecksViewDom(num);
  }

  function clearPgCheckLogSearch(clNum) {
    const num = Number(clNum);
    if (state.checkLogSearchByCl) {
      state.checkLogSearchByCl[num] = "";
    }
    rerenderPgChecksViewDom(num);
  }

  function openPgCheckBuganizer(clNum, targetName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const c = state.changes.find(x => Number(x.number) === num);
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const t = (targetName ? targets.find(x => x.name === targetName) : null) ||
              targets.find(x => x.name === (state.selectedCheckRunByCl && state.selectedCheckRunByCl[num])) ||
              targets[0];
    if (!t || !c) return;

    const ps = Number(c.patch_set || 1);
    const kindLabel = PRESUBMIT_KIND_LABELS[t.kind] || "TARGET";
    const pv = getClPresubmitDisplay(c);

    const title = `[TreeHugger Failure] ${t.name} on ag/${num} (PS ${ps})`;
    const description = [
      `TreeHugger Presubmit Failure Report`,
      `===================================`,
      `Change: https://android-review.googlesource.com/c/${c.project || "platform/hardware/google/aemu"}/+/${num}/${ps}`,
      `Subject: ${c.subject || ""}`,
      `Target: ${t.name} (${kindLabel})`,
      `Status: ${t.raw_status || t.status || "FAIL"} (${t.blocking_level || "BLOCKING"})`,
      `Duration: ${t.duration_display || "—"}`,
      `Branch: ${t.branch || c.branch || "goog/studio-main"}`,
      `Workplan: ${pv.workplanUrl || "N/A"}`,
      `Invocation ID: ${t.artifact_id || "N/A"}`,
      `Sponge URL: ${t.url || pv.checksUrl || "N/A"}`,
      `Secondary Log URL: ${t.secondary_url || "N/A"}`,
      ``,
      `Failure Details:`,
      `${t.subtitle || "Target exited with non-zero failure code."}`,
    ].join("\n");

    const queryParts = [
      "title=" + encodeURIComponent(title),
      "description=" + encodeURIComponent(description),
      "component=192706",
      "format=PLAIN",
    ];
    const bugUrl = "https://b.corp.google.com/issues/new?" + queryParts.join("&");
    if (typeof window !== "undefined" && typeof window.open === "function") {
      window.open(bugUrl, "_blank", "noopener,noreferrer");
    }
    if (typeof showToast === "function") {
      showToast(`Opened Buganizer template for ${t.name} ↗`);
    }
  }

  function copyPgCheckDiagnostics(clNum, targetName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const c = state.changes.find(x => Number(x.number) === num);
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const t = (targetName ? targets.find(x => x.name === targetName) : null) ||
              targets.find(x => x.name === (state.selectedCheckRunByCl && state.selectedCheckRunByCl[num])) ||
              targets[0];
    if (!t || !c) return;

    const ps = Number(c.patch_set || 1);
    const kindLabel = PRESUBMIT_KIND_LABELS[t.kind] || "TARGET";
    const pv = getClPresubmitDisplay(c);

    const md = [
      `### TreeHugger Check Diagnostic: \`${t.name}\``,
      `- **Change**: [ag/${num}](https://android-review.googlesource.com/c/${c.project || "platform/hardware/google/aemu"}/+/${num}/${ps}) (Patchset ${ps})`,
      `- **Subject**: ${c.subject || ""}`,
      `- **Target**: \`${t.name}\` (\`${kindLabel}\`)`,
      `- **Status**: **${t.raw_status || t.status || "FAIL"}** (\`${t.blocking_level || "BLOCKING"}\`)`,
      `- **Duration**: ${t.duration_display || "—"}`,
      `- **Invocation ID**: \`${t.artifact_id || "N/A"}\``,
      `- **Workplan**: ${pv.workplanUrl || "N/A"}`,
      `- **Sponge / Logs**: ${t.url || pv.checksUrl || "N/A"}`,
      t.secondary_url ? `- **Build Logs**: ${t.secondary_url}` : "",
      `- **Details**: ${t.subtitle || "Target exited with non-zero status."}`,
    ].filter(Boolean).join("\n");

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(md).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(`Copied diagnostics for ${t.name} to clipboard ✓`);
    }
  }

  function copyPgCheckLog(clNum, targetName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const c = state.changes.find(x => Number(x.number) === num);
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const t = (targetName ? targets.find(x => x.name === targetName) : null) ||
              targets.find(x => x.name === (state.selectedCheckRunByCl && state.selectedCheckRunByCl[num])) ||
              targets[0];
    if (!t || !c) return;
    const pv = getClPresubmitDisplay(c);
    const lines = generatePgCheckRunLogLines(c, t, pv);
    const fullText = lines.map(l => l.text).join("\n");
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(fullText).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(`Copied console log for ${t.name} to clipboard ✓`);
    }
  }

  function downloadPgCheckLog(clNum, targetName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const c = state.changes.find(x => Number(x.number) === num);
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const t = (targetName ? targets.find(x => x.name === targetName) : null) ||
              targets.find(x => x.name === (state.selectedCheckRunByCl && state.selectedCheckRunByCl[num])) ||
              targets[0];
    if (!t || !c) return;
    const pv = getClPresubmitDisplay(c);
    const lines = generatePgCheckRunLogLines(c, t, pv);
    const fullText = lines.map(l => l.text).join("\n");
    if (typeof Blob !== "undefined" && typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
      const blob = new Blob([fullText], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      const safeName = (t.name || "target").replace(/[^a-zA-Z0-9_\-]/g, "_");
      a.download = `treehugger_${num}_ps${c.patch_set || 1}_${safeName}.log`;
      a.click();
    }
    if (typeof showToast === "function") {
      showToast(`Downloaded log for ${t.name} ✓`);
    }
  }

  function onPgChecksSearchInput(clNum, val) {
    const num = Number(clNum);
    state.checksSearchQueryByCl = state.checksSearchQueryByCl || {};
    state.checksSearchQueryByCl[num] = val;
    rerenderPgChecksViewDom(num);
  }

  function clearPgChecksSearch(clNum) {
    const num = Number(clNum);
    if (state.checksSearchQueryByCl) {
      state.checksSearchQueryByCl[num] = "";
    }
    if (state.checksKindFilterByCl) {
      state.checksKindFilterByCl[num] = "all";
    }
    rerenderPgChecksViewDom(num);
  }

  function rerenderPgChecksViewDom(clNum) {
    const num = Number(clNum);
    const pane = document.getElementById(`pgChecksTabPane-${num}`);
    const c = state.changes.find(x => Number(x.number) === num);
    if (pane && c) {
      pane.innerHTML = renderPgChecksViewHtml(c);
      const input = document.getElementById(`pgChecksSearchInput-${num}`);
      if (input && state.checksSearchQueryByCl && state.checksSearchQueryByCl[num]) {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }
      const logInput = document.getElementById(`pgCheckLogFilterInput-${num}`);
      if (logInput && state.checkLogSearchByCl && state.checkLogSearchByCl[num]) {
        logInput.focus();
        logInput.setSelectionRange(logInput.value.length, logInput.value.length);
      }
    }
  }

  let _pgChecksRerunState = {
    clNum: 0,
    targetName: "",
    mode: "selected",
    selectedTargets: new Set(),
    reason: "",
    bypassCache: false,
    stagePr: true,
    filterText: "",
  };

  function openPgChecksRerunModal(clNum, options = {}) {
    const num = Number(clNum || state.selectedClNumber || (state.changes[0] && state.changes[0].number) || 0);
    const c = state.changes.find(x => Number(x.number) === num);
    if (!num || !c) return;

    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const counts = (entry && entry.data && entry.data.counts) || { total: targets.length, failed: 0, warning: 0 };
    const failWarnCnt = counts.failed + counts.warning;

    let targetName = options.target || "";
    if (!targetName && state.selectedCheckRunByCl && state.selectedCheckRunByCl[num]) {
      targetName = state.selectedCheckRunByCl[num];
    }
    if (!targetName && targets.length > 0) {
      const activeT = targets.find(t => t.status === "failed" || t.status === "warning") || targets[0];
      targetName = activeT ? activeT.name : "";
    }

    let mode = options.mode || (targetName ? "selected" : (failWarnCnt > 0 ? "failed" : "all"));
    if (mode === "failed" && failWarnCnt === 0) mode = "all";

    _pgChecksRerunState = {
      clNum: num,
      targetName: targetName,
      mode: mode,
      selectedTargets: new Set(targets.map(t => t.name)),
      reason: "",
      bypassCache: false,
      stagePr: !c.viewer_presubmit_ready,
      filterText: "",
    };

    renderPgChecksRerunModalDom();

    const backdrop = document.getElementById("pgChecksRerunBackdrop");
    if (backdrop) {
      backdrop.classList.add("is-open");
      backdrop.setAttribute("aria-hidden", "false");
    }
  }

  function closePgChecksRerunModal() {
    const backdrop = document.getElementById("pgChecksRerunBackdrop");
    if (backdrop) {
      backdrop.classList.remove("is-open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgChecksRerunBackdropClick(e) {
    if (e.target && e.target.id === "pgChecksRerunBackdrop") {
      closePgChecksRerunModal();
    }
  }

  function setPgChecksRerunMode(mode) {
    _pgChecksRerunState.mode = mode;
    renderPgChecksRerunModalDom();
  }

  function togglePgChecksRerunTarget(targetName) {
    if (_pgChecksRerunState.selectedTargets.has(targetName)) {
      _pgChecksRerunState.selectedTargets.delete(targetName);
    } else {
      _pgChecksRerunState.selectedTargets.add(targetName);
    }
    updatePgChecksRerunFooterStats();
  }

  function selectAllPgChecksRerunTargets(selectAll) {
    const num = _pgChecksRerunState.clNum;
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    if (selectAll) {
      _pgChecksRerunState.selectedTargets = new Set(targets.map(t => t.name));
    } else {
      _pgChecksRerunState.selectedTargets.clear();
    }
    const listEl = document.getElementById("pgRerunCustomList");
    if (listEl) {
      listEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = selectAll;
      });
    }
    updatePgChecksRerunFooterStats();
  }

  function onPgChecksRerunFilterInput(val) {
    _pgChecksRerunState.filterText = String(val || "").trim().toLowerCase();
    const listEl = document.getElementById("pgRerunCustomList");
    if (listEl) {
      listEl.querySelectorAll(".pg-rerun-custom-item").forEach(item => {
        const name = (item.getAttribute("data-target") || "").toLowerCase();
        item.style.display = (!val || name.includes(_pgChecksRerunState.filterText)) ? "flex" : "none";
      });
    }
  }

  function updatePgChecksRerunFooterStats() {
    const statsEl = document.getElementById("pgChecksRerunFooterStats");
    if (!statsEl) return;
    const num = _pgChecksRerunState.clNum;
    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const counts = (entry && entry.data && entry.data.counts) || { total: targets.length, failed: 0, warning: 0 };
    const failWarnCnt = counts.failed + counts.warning;

    let cnt = 0;
    if (_pgChecksRerunState.mode === "selected") {
      cnt = _pgChecksRerunState.targetName ? 1 : 0;
    } else if (_pgChecksRerunState.mode === "failed") {
      cnt = failWarnCnt;
    } else if (_pgChecksRerunState.mode === "all") {
      cnt = targets.length;
    } else if (_pgChecksRerunState.mode === "custom") {
      cnt = _pgChecksRerunState.selectedTargets.size;
    }
    statsEl.textContent = `Rerunning ${cnt} target${cnt === 1 ? "" : "s"}`;
  }

  function renderPgChecksRerunModalDom() {
    const num = _pgChecksRerunState.clNum;
    const c = state.changes.find(x => Number(x.number) === num);
    if (!c) return;

    const ps = Number(c.patch_set || 1);
    const titleEl = document.getElementById("pgChecksRerunTitle");
    const subEl = document.getElementById("pgChecksRerunSubtitle");
    const bodyEl = document.getElementById("pgChecksRerunBody");

    if (titleEl) titleEl.textContent = "Rerun Presubmit Checks";
    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""} · Patchset ${ps} · TreeHugger CI`;

    if (!bodyEl) return;

    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const counts = (entry && entry.data && entry.data.counts) || { total: targets.length, failed: 0, warning: 0 };
    const failWarnCnt = counts.failed + counts.warning;
    const targetName = _pgChecksRerunState.targetName;
    const curMode = _pgChecksRerunState.mode;

    let customBoxHtml = "";
    if (curMode === "custom") {
      const targetItemsHtml = targets.map(t => {
        const isChecked = _pgChecksRerunState.selectedTargets.has(t.name);
        const kindLabel = PRESUBMIT_KIND_LABELS[t.kind] || "TARGET";
        return `<label class="pg-rerun-custom-item" data-target="${escapeHtml(t.name)}">
          <input type="checkbox" value="${escapeHtml(t.name)}" ${isChecked ? "checked" : ""} onchange="togglePgChecksRerunTarget('${escapeHtml(t.name).replace(/'/g, "\\'")}')" />
          <span class="pg-checks-kind-badge" style="font-size:10px;">${escapeHtml(kindLabel)}</span>
          ${renderPresubmitStatusPillHtml(t)}
          <span style="font-family:var(--g-mono);font-size:11.5px;word-break:break-all;">${escapeHtml(t.name)}</span>
        </label>`;
      }).join("");

      customBoxHtml = `
        <div class="pg-rerun-custom-box" id="pgRerunCustomBox">
          <div class="pg-rerun-custom-toolbar">
            <input type="text" id="pgRerunCustomFilter" class="pg-rerun-custom-filter" placeholder="Filter targets..." oninput="onPgChecksRerunFilterInput(this.value)" />
            <div style="display:flex;gap:4px;">
              <button type="button" class="pg-btn-subtle" onclick="selectAllPgChecksRerunTargets(true)">All</button>
              <button type="button" class="pg-btn-subtle" onclick="selectAllPgChecksRerunTargets(false)">None</button>
            </div>
          </div>
          <div class="pg-rerun-custom-list" id="pgRerunCustomList">
            ${targetItemsHtml}
          </div>
        </div>
      `;
    }

    bodyEl.innerHTML = `
      <div class="pg-rerun-section">
        <span class="pg-rerun-section-title">Select Check Runs to Rerun</span>
        <div class="pg-rerun-scope-group">
          ${targetName ? `
            <label class="pg-rerun-radio-row">
              <input type="radio" name="pgRerunScope" value="selected" ${curMode === "selected" ? "checked" : ""} onchange="setPgChecksRerunMode('selected')" />
              <span>Only selected target: <strong style="font-family:var(--g-mono);font-size:11.5px;">${escapeHtml(targetName)}</strong></span>
            </label>
          ` : ""}
          ${failWarnCnt > 0 ? `
            <label class="pg-rerun-radio-row">
              <input type="radio" name="pgRerunScope" value="failed" ${curMode === "failed" ? "checked" : ""} onchange="setPgChecksRerunMode('failed')" />
              <span>All failed &amp; warning checks (${failWarnCnt})</span>
            </label>
          ` : ""}
          <label class="pg-rerun-radio-row">
            <input type="radio" name="pgRerunScope" value="all" ${curMode === "all" ? "checked" : ""} onchange="setPgChecksRerunMode('all')" />
            <span>All presubmit checks (${targets.length})</span>
          </label>
          <label class="pg-rerun-radio-row">
            <input type="radio" name="pgRerunScope" value="custom" ${curMode === "custom" ? "checked" : ""} onchange="setPgChecksRerunMode('custom')" />
            <span>Custom target selection</span>
          </label>
        </div>
        ${customBoxHtml}
      </div>

      <div class="pg-rerun-options">
        <div class="pg-rerun-input-wrap">
          <label class="pg-rerun-input-label" for="pgRerunReasonInput">Reason / Notes (Optional)</label>
          <input
            type="text"
            id="pgRerunReasonInput"
            class="pg-rerun-reason-input"
            placeholder="e.g. Flake reproduction, fresh worker run..."
            value="${escapeHtml(_pgChecksRerunState.reason)}"
            oninput="_pgChecksRerunState.reason = this.value"
          />
        </div>
        <label class="pg-rerun-checkbox-row">
          <input
            type="checkbox"
            id="pgRerunBypassCache"
            ${_pgChecksRerunState.bypassCache ? "checked" : ""}
            onchange="_pgChecksRerunState.bypassCache = this.checked"
          />
          <span>Bypass remote cache (clean build/test)</span>
        </label>
        <label class="pg-rerun-checkbox-row">
          <input
            type="checkbox"
            id="pgRerunStagePr"
            ${_pgChecksRerunState.stagePr ? "checked" : ""}
            onchange="_pgChecksRerunState.stagePr = this.checked"
          />
          <span>Ensure Presubmit-Ready (PR +1) is active</span>
        </label>
      </div>
    `;

    updatePgChecksRerunFooterStats();
  }

  async function confirmPgChecksRerun() {
    const num = _pgChecksRerunState.clNum;
    const c = state.changes.find(x => Number(x.number) === num);
    if (!num || !c) return;

    const entry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[num];
    const targets = (entry && entry.data && entry.data.targets) || [];
    const ps = Number(c.patch_set || 1);

    let targetsToRerun = [];
    if (_pgChecksRerunState.mode === "selected") {
      targetsToRerun = _pgChecksRerunState.targetName ? [_pgChecksRerunState.targetName] : [];
    } else if (_pgChecksRerunState.mode === "failed") {
      targetsToRerun = targets.filter(t => t.status === "failed" || t.status === "warning").map(t => t.name);
    } else if (_pgChecksRerunState.mode === "all") {
      targetsToRerun = targets.map(t => t.name);
    } else if (_pgChecksRerunState.mode === "custom") {
      targetsToRerun = Array.from(_pgChecksRerunState.selectedTargets);
    }

    if (targetsToRerun.length === 0) {
      if (typeof showToast === "function") showToast("No targets selected to rerun");
      return;
    }

    const reason = _pgChecksRerunState.reason || "";
    const bypassCache = Boolean(_pgChecksRerunState.bypassCache);
    const stagePr = Boolean(_pgChecksRerunState.stagePr);

    const targetSet = new Set(targetsToRerun);
    targets.forEach(t => {
      if (targetSet.has(t.name)) {
        t.status = "queued";
        t.raw_status = "QUEUED (RERUN)";
        t.waiting_on_build = true;
        t.duration_display = "0s";
        t.cached = false;
      }
    });

    if (entry && entry.data && entry.data.counts) {
      const cnts = entry.data.counts;
      cnts.failed = targets.filter(t => t.status === "failed").length;
      cnts.warning = targets.filter(t => t.status === "warning").length;
      cnts.running = targets.filter(t => t.status === "running").length;
      cnts.queued = targets.filter(t => t.status === "queued").length;
      cnts.passed = targets.filter(t => t.status === "passed").length;
    }

    state.rerunLogsByCl = state.rerunLogsByCl || {};
    state.rerunLogsByCl[num] = state.rerunLogsByCl[num] || {};
    const who = state.viewer || "user";
    targetsToRerun.forEach(tName => {
      state.rerunLogsByCl[num][tName] = [
        { type: "info", text: `[00:00:00] [RERUN] Manual rerun initiated by ${who}${reason ? ` (Reason: ${reason})` : ""}${bypassCache ? " [bypass_cache=true]" : ""}` },
        { type: "running", text: `[00:00:01] ◷ [QUEUED] Job scheduled on TreeHugger build worker` },
      ];
    });

    closePgChecksRerunModal();
    rerenderPgChecksViewDom(num);

    const desc = targetsToRerun.length === 1 ? targetsToRerun[0] : `${targetsToRerun.length} checks`;
    if (typeof showToast === "function") {
      showToast(`Triggered rerun for ${desc} on ag/${num} (PS ${ps}) ✓`);
    }

    if (typeof sidecarFetch === "function") {
      try {
        await sidecarFetch("/api/gerrit/checks/rerun", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            cl: num,
            targets: targetsToRerun,
            ps: ps,
            reason: reason,
            bypass_cache: bypassCache,
            stage_presubmit_ready: stagePr,
          }),
        });
      } catch (err) {
        console.warn("Checks rerun backend error:", err);
      }
    }
  }

  function triggerPgSingleCheckRerun(clNum, targetName) {
    const num = Number(clNum || state.selectedClNumber || (state.changes[0] && state.changes[0].number) || 0);
    const c = state.changes.find(x => Number(x.number) === num);
    if (!num || !c || !targetName) return;

    _pgChecksRerunState = {
      clNum: num,
      targetName: targetName,
      mode: "selected",
      selectedTargets: new Set([targetName]),
      reason: "",
      bypassCache: false,
      stagePr: !c.viewer_presubmit_ready,
      filterText: "",
    };
    confirmPgChecksRerun();
  }

  function formatLiveTargetsSummaryText(c, pv) {
    const entry = state.presubmitTargetsByCl[Number(c.number)];
    const counts = entry && entry.data && entry.data.counts ? entry.data.counts : null;
    if (counts && counts.total > 0) {
      const parts = [];
      if (counts.failed > 0) parts.push(`${counts.failed} failed`);
      if (counts.warning > 0) parts.push(`${counts.warning} warn`);
      if (counts.running > 0) parts.push(`${counts.running} running`);
      if (counts.queued > 0) parts.push(`${counts.queued} queued`);
      if (counts.passed > 0) {
        parts.push(counts.cached > 0 ? `${counts.passed} passed (${counts.cached} cached)` : `${counts.passed} passed`);
      }
      return parts.join(" · ");
    }
    return pv.summary || "";
  }

  function resolvePgChecksChipClassAndLabel(pv) {
    if (pv.status === "passed") return { cls: "st-passed", text: pv.cleanLabel || "✓ Passed" };
    if (pv.status === "failed") return { cls: "st-failed", text: pv.cleanLabel || "✗ Failed" };
    if (pv.status === "warning") return { cls: "st-warning", text: pv.cleanLabel || "⚠ Warning" };
    if (pv.status === "running" || pv.status === "queued") {
      return { cls: "st-running", text: pv.cleanLabel || "Running" };
    }
    return { cls: "st-idle", text: "Not run" };
  }

  function renderCommitPresubmitRowHtml(c) {
    const num = Number(c.number);
    const pv = getClPresubmitDisplay(c);
    const chipInfo = resolvePgChecksChipClassAndLabel(pv);
    const isExpanded = state.expandedPresubmitCls.has(num);
    const entry = state.presubmitTargetsByCl[num];
    const totalTargets = entry && entry.data && entry.data.counts ? entry.data.counts.total : null;
    const countSuffix = totalTargets !== null ? ` (${totalTargets})` : "";
    const curTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[num]) || "files";
    const toggleLabel = curTab === "checks" ? "Files Tab ↗" : `Checks Tab${countSuffix} ↗`;
    const summaryText = formatLiveTargetsSummaryText(c, pv);
    const summaryHtml = summaryText
      ? `<span style="font-size:11.5px;color:var(--g-muted);">${escapeHtml(summaryText)}</span>`
      : "";
    const wpLinkHtml = pv.workplanUrl
      ? `<a class="ag-link" style="font-size:11.5px;" href="${escapeHtml(pv.workplanUrl)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">Workplan ${escapeHtml(pv.workplanId)} ↗</a>`
      : "";
    const checksLinkHtml = pv.checksUrl
      ? `<a class="ag-link" style="font-size:11.5px;" href="${escapeHtml(pv.checksUrl)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">Checks ↗</a>`
      : "";
    const bundle = state.diffsByCl[num];
    const bundleUnres = (bundle && Array.isArray(bundle.unresolved_threads))
      ? bundle.unresolved_threads.filter(t => t.unresolved !== false).length
      : 0;
    const unresCnt = Math.max(Number(c.unresolved_comment_count || 0), bundleUnres);
    const commentsValHtml = unresCnt > 0
      ? `<span class="pg-summary-unresolved">💬 ${unresCnt} unresolved</span>`
      : `<span style="font-size:12px;color:var(--g-muted);">No unresolved comments</span>`;

    const counts = entry && entry.data && entry.data.counts;
    if (!counts) {
      const prSub = c._prRowSub;
      if (
        prSub &&
        prSub.num === num &&
        prSub.curTab === curTab &&
        prSub.unresCnt === unresCnt &&
        prSub.chipCls === chipInfo.cls &&
        prSub.chipText === chipInfo.text &&
        prSub.hasSpinner === Boolean(pv.spinnerHtml) &&
        prSub.tooltip === (pv.tooltip || "") &&
        prSub.summaryText === (summaryText || "") &&
        prSub.wpUrl === (pv.workplanUrl || "") &&
        prSub.wpId === (pv.workplanId || "") &&
        prSub.checksUrl === (pv.checksUrl || "") &&
        c._cachedPresubmitRowKey === prSub.key &&
        typeof c._cachedPresubmitRowHtml === "string"
      ) {
        return c._cachedPresubmitRowHtml;
      }
    }
    const countsSig = counts
      ? `${counts.total || 0}:${counts.failed || 0}:${counts.warning || 0}:${counts.running || 0}:${counts.queued || 0}:${counts.passed || 0}:${counts.cached || 0}`
      : "";
    const rowCacheKey = `${num}|${curTab}|${unresCnt}|${chipInfo.cls}|${chipInfo.text}|${pv.spinnerHtml ? 1 : 0}|${pv.tooltip || ""}|${summaryText || ""}|${pv.workplanUrl || ""}|${pv.workplanId || ""}|${pv.checksUrl || ""}|${countsSig}`;
    if (c._cachedPresubmitRowKey === rowCacheKey && typeof c._cachedPresubmitRowHtml === "string") {
      return c._cachedPresubmitRowHtml;
    }
    let checksValHtml = "";
    if (counts && counts.total > 0) {
      const summaryChips = [];
      if (counts.failed > 0) {
        summaryChips.push(`<button type="button" class="pg-checks-summary-chip chip-failed" onclick="event.stopPropagation(); selectPgChecksStatusFilter(${num}, 'failed')" title="Filter Checks tab to ${counts.failed} failed check(s)"><span class="chip-icon">✕</span> ${counts.failed} failed</button>`);
      }
      if (counts.warning > 0) {
        summaryChips.push(`<button type="button" class="pg-checks-summary-chip chip-warning" onclick="event.stopPropagation(); selectPgChecksStatusFilter(${num}, 'warning')" title="Filter Checks tab to ${counts.warning} warning check(s)"><span class="chip-icon">⚠</span> ${counts.warning} warning</button>`);
      }
      if ((counts.running + counts.queued) > 0) {
        summaryChips.push(`<button type="button" class="pg-checks-summary-chip chip-running" onclick="event.stopPropagation(); selectPgChecksStatusFilter(${num}, 'running')" title="Filter Checks tab to ${counts.running + counts.queued} in-progress check(s)"><span class="chip-icon pg-checks-spin">◷</span> ${counts.running + counts.queued} running</button>`);
      }
      if (counts.passed > 0) {
        summaryChips.push(`<button type="button" class="pg-checks-summary-chip chip-passed" onclick="event.stopPropagation(); selectPgChecksStatusFilter(${num}, 'passed')" title="Filter Checks tab to ${counts.passed} passed check(s)"><span class="chip-icon">✓</span> ${counts.passed} passed</button>`);
      }
      if (counts.cached > 0) {
        summaryChips.push(`<span class="pg-checks-summary-cached" title="${counts.cached} target(s) completed from remote build cache">⚡ ${counts.cached} cached</span>`);
      }
      const rerunFailedBtn = counts.failed > 0
        ? `<button type="button" class="pg-btn-subtle pg-summary-rerun-btn" onclick="event.stopPropagation(); openPgChecksRerunModal(${num}, { mode: 'failed' })" title="Rerun failed presubmit checks">↻ Rerun failed</button>`
        : "";
      checksValHtml = `<div class="pg-checks-summary-chips">
        ${summaryChips.join("")}
        ${rerunFailedBtn}
        <button type="button" class="add-participant-btn" onclick="event.stopPropagation(); selectPgChangeTab(${num}, 'checks')">${escapeHtml(toggleLabel)}</button>
        ${wpLinkHtml}
        ${checksLinkHtml}
      </div>`;
    } else {
      checksValHtml = `<button type="button" class="pg-checks-chip ${chipInfo.cls}" onclick="event.stopPropagation(); selectPgChangeTab(${num}, 'checks')" title="${escapeHtml(pv.tooltip || "Click to inspect in Checks tab")}">${pv.spinnerHtml}${escapeHtml(chipInfo.text)}</button>
        ${summaryHtml}
        <button type="button" class="add-participant-btn" onclick="event.stopPropagation(); selectPgChangeTab(${num}, 'checks')">${escapeHtml(toggleLabel)}</button>
        ${wpLinkHtml}
        ${checksLinkHtml}`;
    }

    const rowHtml = `<div class="pg-change-summary">
      <div class="pg-summary-row">
        <span class="pg-summary-key">Comments</span>
        <div class="pg-summary-val">${commentsValHtml}</div>
      </div>
      <div class="pg-summary-row">
        <span class="pg-summary-key">Checks</span>
        <div class="pg-summary-val">${checksValHtml}</div>
      </div>
    </div>`;
    c._cachedPresubmitRowKey = rowCacheKey;
    c._cachedPresubmitRowHtml = rowHtml;
    if (!counts) {
      c._prRowSub = {
        num, curTab, unresCnt, chipCls: chipInfo.cls, chipText: chipInfo.text,
        hasSpinner: Boolean(pv.spinnerHtml), tooltip: pv.tooltip || "",
        summaryText: summaryText || "", wpUrl: pv.workplanUrl || "",
        wpId: pv.workplanId || "", checksUrl: pv.checksUrl || "", key: rowCacheKey
      };
    }
    return rowHtml;
  }

  function rerenderPresubmitDrawerDom(clNum) {
    const num = Number(clNum);
    const clObj = state.changes.find(x => Number(x.number) === num);
    if (!clObj) return;
    const rowSlot = document.getElementById(`presubmitRowSlot-${num}`);
    if (rowSlot) {
      rowSlot.innerHTML = renderCommitPresubmitRowHtml(clObj);
    }
    const checksTab = document.getElementById(`pgTabChecks-${num}`);
    if (checksTab) {
      checksTab.outerHTML = renderPgChecksTabButtonHtml(clObj);
    }
    const checksPane = document.getElementById(`pgChecksTabPane-${num}`);
    if (checksPane) {
      checksPane.innerHTML = renderPgChecksViewHtml(clObj);
    }
    const checksControls = document.getElementById(`pgChecksControls-${num}`);
    if (checksControls) {
      checksControls.innerHTML = renderPgChecksControlsHtml(clObj);
    }
    const drawerSlot = document.getElementById(`presubmitDrawerSlot-${num}`);
    if (drawerSlot) {
      drawerSlot.innerHTML = renderPresubmitTargetsDrawerHtml(clObj);
    }
    const srSlot = document.getElementById(`pgSrSlot-${num}`);
    if (srSlot) {
      srSlot.innerHTML = renderPgSubmitRequirementsHtml(clObj);
    }
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileTableWrap = document.querySelector(`#pgFilesTabPane-${num} .pg-file-list-table-wrap`);
    if (fileTableWrap && typeof renderPgFileListHtml === "function") {
      fileTableWrap.outerHTML = renderPgFileListHtml(clObj, bundle);
    }
    const pv = getClPresubmitDisplay(clObj);
    const pvBodyHtml = renderPgPvVoteBodyHtml(clObj);
    document.querySelectorAll(`[data-pv-cl="${num}"]`).forEach(pvBtn => {
      pvBtn.title = pv.tooltip;
      pvBtn.innerHTML = pvBodyHtml;
    });
    if (Number(state.selectedClNumber) === num) {
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
    }
  }

  function togglePresubmitTargets(clNum) {
    const num = Number(clNum);
    const curTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[num]) || "files";
    const nextTab = curTab === "checks" ? "files" : "checks";
    selectPgChangeTab(num, nextTab);
    ensurePresubmitTargetsPollingTimer();
  }

  function openPresubmitTargetsFromCard(stackId, clNum) {
    const num = Number(clNum);
    state.activeChangeTabByCl = state.activeChangeTabByCl || {};
    state.activeChangeTabByCl[num] = "checks";
    const alreadySelected = Number(state.selectedClNumber) === num && state.selectedStackId === stackId;
    if (alreadySelected) {
      rerenderPresubmitDrawerDom(num);
      fetchPresubmitTargetsForCl(num);
      ensurePresubmitTargetsPollingTimer();
    } else {
      selectStackAndCl(stackId, num);
    }
    setTimeout(() => {
      const tabsBar = document.getElementById(`pgTabsBar-${num}`);
      if (tabsBar && typeof tabsBar.scrollIntoView === "function") {
        tabsBar.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }, 60);
  }

  function setPresubmitTargetFilter(clNum, filterId) {
    const num = Number(clNum);
    state.presubmitTargetFilterByCl[num] = filterId || "all";
    rerenderPresubmitDrawerDom(num);
  }

  async function fetchPresubmitTargetsForCl(clNum, opts = {}) {
    const num = Number(clNum);
    if (!num || (window.__GERRIT_UI_BOOTSTRAP__ && window.__GERRIT_UI_BOOTSTRAP__.changes)) return;
    const prev = state.presubmitTargetsByCl[num];
    if (prev && prev.loading && !opts.forceRefresh) return;
    const clObj = state.changes.find(x => Number(x.number) === num);
    const ps = clObj && clObj.patch_set ? Number(clObj.patch_set) : 0;
    const project = (clObj && clObj.project) || "";
    state.nextPresubmitReqSeq = (state.nextPresubmitReqSeq || 0) + 1;
    const reqSeq = state.nextPresubmitReqSeq;
    state.presubmitTargetsByCl[num] = {
      loading: true,
      error: "",
      data: prev ? prev.data : null,
      reqSeq,
    };
    rerenderPresubmitDrawerDom(num);
    try {
      const qsParts = [`cl=${encodeURIComponent(num)}`];
      if (ps > 0) qsParts.push(`ps=${encodeURIComponent(ps)}`);
      if (project) qsParts.push(`project=${encodeURIComponent(project)}`);
      if (opts.forceRefresh) qsParts.push("refresh=1");
      const resp = await sidecarFetch(`/api/gerrit/presubmit-targets?${qsParts.join("&")}`);
      const data = await resp.json();
      const cur = state.presubmitTargetsByCl[num];
      if (!cur || cur.reqSeq !== reqSeq) return;
      if (!resp.ok || data.error || data.status !== "ok") {
        throw new Error(data.error || `HTTP ${resp.status} while querying presubmit targets for ag/${num}`);
      }
      state.presubmitTargetsByCl[num] = { loading: false, error: "", data, reqSeq };
      if (!state.presubmitTargetFilterByCl[num] && data.counts && (data.counts.failed + data.counts.warning) > 0) {
        state.presubmitTargetFilterByCl[num] = "all";
      }
      rerenderPresubmitDrawerDom(num);
      ensurePresubmitTargetsPollingTimer();
    } catch (err) {
      const cur = state.presubmitTargetsByCl[num];
      if (!cur || cur.reqSeq !== reqSeq) return;
      state.presubmitTargetsByCl[num] = {
        loading: false,
        error: err.message || String(err),
        data: cur.data,
        reqSeq,
      };
      rerenderPresubmitDrawerDom(num);
    }
  }

  function getExpandedActivePresubmitCls() {
    const out = [];
    const activeNums = new Set(state.expandedPresubmitCls);
    if (state.activeChangeTabByCl) {
      Object.keys(state.activeChangeTabByCl).forEach(n => {
        if (state.activeChangeTabByCl[n] === "checks") activeNums.add(Number(n));
      });
    }
    activeNums.forEach(num => {
      const clObj = state.changes.find(x => Number(x.number) === Number(num));
      const entry = state.presubmitTargetsByCl[Number(num)];
      const runSt = entry && entry.data ? entry.data.run_status : "";
      const clSt = clObj ? clObj.presubmit_status : "";
      if (runSt === "running" || runSt === "queued" || clSt === "running" || clSt === "queued") {
        out.push(Number(num));
      }
    });
    return out;
  }

  function ensurePresubmitTargetsPollingTimer() {
    if (window._presubmitTargetsPollInterval) return;
    if (getExpandedActivePresubmitCls().length === 0) return;
    window._presubmitTargetsPollInterval = setInterval(() => {
      const activeNums = getExpandedActivePresubmitCls();
      if (activeNums.length === 0) {
        clearInterval(window._presubmitTargetsPollInterval);
        window._presubmitTargetsPollInterval = null;
        return;
      }
      activeNums.forEach(num => fetchPresubmitTargetsForCl(num, { forceRefresh: true }));
    }, PRESUBMIT_POLL_INTERVAL_MS);
  }

  function renderInViewerStagedVoteRowHtml(opts = {}) {
    const inBatchPanel = Boolean(opts && opts.inBatchPanel);
    const batchStripHtml = inBatchPanel
      ? ""
      : `<div id="batchVoteHeaderStrip" class="batch-vote-header-strip" style="display:none;"></div>`;
    return `<div class="participants-row gerrit-vote-row">
      <span id="voteRowTitle" class="participants-label">${inBatchPanel ? "Batch Vote" : "Your Vote"}</span>
      <div id="stagedVoteBar" class="gerrit-vote-panel" onclick="event.stopPropagation()">
        ${batchStripHtml}
        <div class="gerrit-vote-controls-row">
          <div class="staged-label-group" role="group" aria-label="Code-Review vote">
            <span class="staged-label-title" title="Code-Review label">Code-Review</span>
            <button id="stageCrNeg2" type="button" class="stage-cr-btn cr-neg" data-cr="-2" onclick="toggleStagedCr(-2)" title="Stage Code-Review -2">-2</button>
            <button id="stageCrNeg1" type="button" class="stage-cr-btn cr-neg" data-cr="-1" onclick="toggleStagedCr(-1)" title="Stage Code-Review -1">-1</button>
            <button id="stageCrZero" type="button" class="stage-cr-btn cr-zero" data-cr="0" onclick="toggleStagedCr(0)" title="Stage Code-Review 0 (Shortcut: 0)">0</button>
            <button id="stageCrPlus1" type="button" class="stage-cr-btn cr-pos1" data-cr="1" onclick="toggleStagedCr(1)" title="Stage Code-Review +1 (Shortcut: 1)">+1</button>
            <button id="stageCrPlus2" type="button" class="stage-cr-btn cr-pos2" data-cr="2" onclick="toggleStagedCr(2)" title="Stage Code-Review +2 (Shortcut: 2 or a)">+2</button>
          </div>
          <div class="staged-label-group" role="group" aria-label="Verified vote">
            <span class="staged-label-title" title="Verified label">Verified</span>
            <button id="stageVNeg1" type="button" class="stage-cr-btn cr-neg" data-v="-1" onclick="toggleStagedVerified(-1)" title="Stage Verified -1">-1</button>
            <button id="stageVZero" type="button" class="stage-cr-btn cr-zero" data-v="0" onclick="toggleStagedVerified(0)" title="Stage Verified 0">0</button>
            <button id="stageVPlus1" type="button" class="stage-cr-btn cr-pos1" data-v="1" onclick="toggleStagedVerified(1)" title="Stage Verified +1 (Shortcut: Alt+V)">+1</button>
          </div>
          <label id="stagePrPill" class="staged-check-pill" title="Check to stage Presubmit-Ready +1 (Shortcut: p)">
            <input id="stagePrCheckbox" type="checkbox" onclick="event.stopPropagation(); toggleStagedPr(this.checked)" />
            <span id="stagePrLabel">Presubmit-Ready +1</span>
          </label>
          <label id="stageAsPill" class="staged-check-pill" title="Check to stage Autosubmit +1 (Shortcut: u)">
            <input id="stageAsCheckbox" type="checkbox" onclick="event.stopPropagation(); toggleStagedAs(this.checked)" />
            <span id="stageAsLabel">Autosubmit +1</span>
          </label>
          <button id="btnToggleVoteNote" type="button" class="btn btn-vote-note-toggle" onclick="toggleStagedVoteNote()" title="Attach an optional top-level review comment with your vote">💬 Comment</button>
          <button id="btnToggleExtendedVoteLabels" type="button" class="btn btn-toggle-extended-vote" onclick="toggleExtendedVoteControls()" title="Toggle extended review labels (+ More labels)">+ More labels ▾</button>
          <button id="btnSubmitVotes" type="button" class="btn btn-submit-votes" onclick="submitStagedVotes(this)" disabled title="Submit all staged votes in a single Gerrit request (Shortcut: Enter)">Submit Vote</button>
          <button id="btnResetStagedVotes" type="button" class="btn btn-reset-staged" style="display:none;" onclick="resetStagedVote()" title="Discard unsent vote selections">Reset</button>
        </div>
        <div id="stagedVoteExtendedRow" class="staged-vote-extended-row" style="display:none;">
          <div class="staged-label-group" role="group" aria-label="Presubmit-Verified vote">
            <span class="staged-label-title" title="Presubmit-Verified label">Presubmit-Verified</span>
            <button id="stagePvNeg1" type="button" class="stage-cr-btn cr-neg" data-pv="-1" onclick="toggleStagedPresubmitVerified(-1)" title="Stage Presubmit-Verified -1">-1</button>
            <button id="stagePvZero" type="button" class="stage-cr-btn cr-zero" data-pv="0" onclick="toggleStagedPresubmitVerified(0)" title="Stage Presubmit-Verified 0">0</button>
            <button id="stagePvPlus1" type="button" class="stage-cr-btn cr-pos1" data-pv="1" onclick="toggleStagedPresubmitVerified(1)" title="Stage Presubmit-Verified +1">+1</button>
          </div>
          <div class="staged-label-group" role="group" aria-label="Performance vote">
            <span class="staged-label-title" title="Performance label">Performance</span>
            <button id="stagePerfNeg1" type="button" class="stage-cr-btn cr-neg" data-perf="-1" onclick="toggleStagedPerformance(-1)" title="Stage Performance -1">-1</button>
            <button id="stagePerfZero" type="button" class="stage-cr-btn cr-zero" data-perf="0" onclick="toggleStagedPerformance(0)" title="Stage Performance 0">0</button>
            <button id="stagePerfPlus1" type="button" class="stage-cr-btn cr-pos1" data-perf="1" onclick="toggleStagedPerformance(1)" title="Stage Performance +1">+1</button>
          </div>
          <div class="staged-label-group" role="group" aria-label="Lint vote">
            <span class="staged-label-title" title="Lint label">Lint</span>
            <button id="stageLintNeg1" type="button" class="stage-cr-btn cr-neg" data-lint="-1" onclick="toggleStagedLint(-1)" title="Stage Lint -1">-1</button>
            <button id="stageLintZero" type="button" class="stage-cr-btn cr-zero" data-lint="0" onclick="toggleStagedLint(0)" title="Stage Lint 0">0</button>
            <button id="stageLintPlus1" type="button" class="stage-cr-btn cr-pos1" data-lint="1" onclick="toggleStagedLint(1)" title="Stage Lint +1">+1</button>
          </div>
          <div class="staged-label-group" role="group" aria-label="Bypass-Presubmit vote">
            <span class="staged-label-title" title="Bypass-Presubmit label">Bypass-Presubmit</span>
            <button id="stageBpZero" type="button" class="stage-cr-btn cr-zero" data-bp="0" onclick="toggleStagedBypassPresubmit(0)" title="Stage Bypass-Presubmit 0">0</button>
            <button id="stageBpPlus1" type="button" class="stage-cr-btn cr-pos1" data-bp="1" onclick="toggleStagedBypassPresubmit(1)" title="Stage Bypass-Presubmit +1">+1</button>
            <button id="stageBpPlus2" type="button" class="stage-cr-btn cr-pos2" data-bp="2" onclick="toggleStagedBypassPresubmit(2)" title="Stage Bypass-Presubmit +2">+2</button>
          </div>
          <div class="staged-label-group" role="group" aria-label="API-Review vote">
            <span class="staged-label-title" title="API-Review label">API-Review</span>
            <button id="stageApiNeg1" type="button" class="stage-cr-btn cr-neg" data-api="-1" onclick="toggleStagedApiReview(-1)" title="Stage API-Review -1">-1</button>
            <button id="stageApiZero" type="button" class="stage-cr-btn cr-zero" data-api="0" onclick="toggleStagedApiReview(0)" title="Stage API-Review 0">0</button>
            <button id="stageApiPlus1" type="button" class="stage-cr-btn cr-pos1" data-api="1" onclick="toggleStagedApiReview(1)" title="Stage API-Review +1">+1</button>
          </div>
          <div class="staged-label-group" role="group" aria-label="Owners-Override vote">
            <span class="staged-label-title" title="Owners-Override label">Owners-Override</span>
            <button id="stageOoZero" type="button" class="stage-cr-btn cr-zero" data-oo="0" onclick="toggleStagedOwnersOverride(0)" title="Stage Owners-Override 0">0</button>
            <button id="stageOoPlus1" type="button" class="stage-cr-btn cr-pos1" data-oo="1" onclick="toggleStagedOwnersOverride(1)" title="Stage Owners-Override +1">+1</button>
          </div>
          <div class="staged-label-group" role="group" aria-label="Rubber-Stamp vote">
            <span class="staged-label-title" title="Rubber-Stamp label">Rubber-Stamp</span>
            <button id="stageRsZero" type="button" class="stage-cr-btn cr-zero" data-rs="0" onclick="toggleStagedRubberStamp(0)" title="Stage Rubber-Stamp 0">0</button>
            <button id="stageRsPlus1" type="button" class="stage-cr-btn cr-pos1" data-rs="1" onclick="toggleStagedRubberStamp(1)" title="Stage Rubber-Stamp +1">+1</button>
          </div>
        </div>
        <div id="stagedVoteNoteRow" class="staged-vote-note-row" style="display:none;">
          <input
            id="stagedVoteCommentInput"
            type="text"
            class="staged-vote-note-input"
            placeholder="Optional top-level review comment (Enter to submit)…"
            oninput="onStagedVoteCommentInput(this.value)"
            onkeydown="onStagedVoteCommentKeydown(event)"
          />
        </div>
      </div>
    </div>`;
  }

  function selectAllBatchCls() {
    if (!Array.isArray(state.changes) || state.changes.length === 0) return;
    state.changes.forEach(c => {
      if (c && c.number) state.checkedClNumbers.add(Number(c.number));
    });
    syncQueueSelectionDom();
    if (typeof showToast === "function") {
      showToast(`Selected all ${state.checkedClNumbers.size} changes for batch review ✓`);
    }
  }

  function toggleSelectAllBatchCls(event) {
    if (event) event.stopPropagation();
    const allChecked = state.changes.length > 0 && state.checkedClNumbers.size >= state.changes.length;
    if (allChecked) {
      clearCheckedCls();
    } else {
      selectAllBatchCls();
    }
  }

  function invertBatchClsSelection() {
    if (!Array.isArray(state.changes) || state.changes.length === 0) return;
    state.changes.forEach(c => {
      if (!c || !c.number) return;
      const n = Number(c.number);
      if (state.checkedClNumbers.has(n)) {
        state.checkedClNumbers.delete(n);
      } else {
        state.checkedClNumbers.add(n);
      }
    });
    syncQueueSelectionDom();
  }

  function renderBatchModePanelHtml(checkedNums) {
    const count = checkedNums.length;
    let totalIns = 0;
    let totalDel = 0;
    checkedNums.forEach(num => {
      const c = state.changes.find(x => Number(x.number) === Number(num));
      if (c) {
        totalIns += Math.max(0, Number(c.insertions || 0));
        totalDel += Math.max(0, Number(c.deletions || 0));
      }
    });
    const deltaSummary = (totalIns + totalDel > 0)
      ? `<span class="pg-batch-card-delta" title="+${totalIns}, -${totalDel} lines across selected changes"><span class="pg-delta-add">+${totalIns}</span>, <span class="pg-delta-del">-${totalDel}</span></span>`
      : "";

    const chipsHtml = checkedNums.map(num => {
      const c = state.changes.find(x => Number(x.number) === Number(num));
      const subj = c ? c.subject : `CL ${num}`;
      const url = c && c.url ? c.url : `https://googleplex-android-review.git.corp.google.com/c/${num}`;
      return `<span class="batch-selected-cl-chip" title="${escapeHtml(`ag/${num} — ${subj}`)}">
        <a class="ag-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">ag/${num} ↗</a>
        <span class="batch-selected-cl-subj">${escapeHtml(subj)}</span>
        <button type="button" class="nudge-btn" style="padding:1px 5px;font-size:10px;" onclick="event.stopPropagation(); inspectSingleClFromBatch(${num})" title="Exit batch mode and open diff for ag/${num}">Diff</button>
        <button type="button" class="batch-selected-cl-remove" onclick="toggleClCheckbox(${num}, event)" title="Uncheck ag/${num}" aria-label="Uncheck ag/${num}">×</button>
      </span>`;
    }).join("");

    const changesRowsHtml = checkedNums.map(num => {
      const c = state.changes.find(x => Number(x.number) === Number(num));
      const subj = c ? c.subject : `CL ${num}`;
      const url = c && c.url ? c.url : `https://googleplex-android-review.git.corp.google.com/c/${num}`;
      const proj = c ? (c.project_short || c.project || "") : "";
      const branch = c ? (c.branch || "") : "";
      const statusTagHtml = c ? renderPgStatusTagHtml(c) : "";
      const ownerHtml = c ? renderPgWhoCellHtml(c, false) : "—";
      const sz = c ? computePgChangeSize(c) : { tier: "—", cls: "size-xs", tooltip: "" };
      const crVoteHtml = c ? renderPgCrVoteCellHtml(c) : "—";
      const vVoteHtml = c ? renderPgVVoteCellHtml(c) : "—";
      return `<div class="pg-batch-change-row" data-cl-num="${num}">
        <input type="checkbox" class="cl-select-checkbox pg-batch-row-checkbox" checked onclick="toggleClCheckbox(${num}, event)" title="Uncheck ag/${num}" aria-label="Uncheck ag/${num}" />
        <a class="pg-batch-cl-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">ag/${num} ↗</a>
        <span id="pgBatchStatusSlot-${num}" class="pg-status-slot">${statusTagHtml}</span>
        <span class="pg-batch-cl-subject" title="${escapeHtml(subj)}">${escapeHtml(subj)}</span>
        ${(proj || branch) ? `<span class="pg-batch-cl-branch" title="${escapeHtml(`${proj} · ${branch}`)}">${escapeHtml(proj ? `${proj} · ` : "")}${escapeHtml(branch)}</span>` : ""}
        <span class="pg-batch-cl-owner" title="Change Owner">${ownerHtml}</span>
        <span class="pg-batch-cl-size" title="${escapeHtml(sz.tooltip)}"><span class="pg-size-badge ${sz.cls}">${sz.tier}</span></span>
        <span class="pg-batch-cl-vote pg-batch-col-cr" title="Code-Review vote">${crVoteHtml}</span>
        <span class="pg-batch-cl-vote pg-batch-col-v" title="Verified vote">${vVoteHtml}</span>
        <div class="pg-batch-row-actions">
          <button type="button" class="pg-btn-flat pg-batch-btn-diff" onclick="event.stopPropagation(); inspectSingleClFromBatch(${num})" title="Exit batch mode and open diff for ag/${num}">Diff ↗</button>
          <button type="button" class="pg-batch-btn-remove" onclick="toggleClCheckbox(${num}, event)" title="Uncheck ag/${num}" aria-label="Uncheck ag/${num}">×</button>
        </div>
      </div>`;
    }).join("");

    const allChecked = state.changes.length > 0 && checkedNums.length >= state.changes.length;
    const tableHeaderHtml = `<div class="pg-batch-table-header" role="row">
      <input type="checkbox" id="pgBatchMasterCheckbox" class="cl-select-checkbox pg-batch-master-checkbox" ${allChecked ? "checked" : ""} onclick="toggleSelectAllBatchCls(event)" title="${allChecked ? "Deselect all changes" : "Select all changes"}" aria-label="Toggle select all changes" />
      <span class="pg-batch-th pg-batch-th-cl">Change</span>
      <span class="pg-batch-th pg-batch-th-status">Status</span>
      <span class="pg-batch-th pg-batch-th-subj">Subject</span>
      <span class="pg-batch-th pg-batch-th-branch">Branch</span>
      <span class="pg-batch-th pg-batch-th-owner">Owner</span>
      <span class="pg-batch-th pg-batch-th-size">Size</span>
      <span class="pg-batch-th pg-batch-th-cr">CR</span>
      <span class="pg-batch-th pg-batch-th-v">V</span>
      <span class="pg-batch-th pg-batch-th-actions">Actions</span>
    </div>`;

    const initialBatchComment = (state.stagedVote && state.stagedVote.comment) ||
      (loadPgReplyDraft("batch") && loadPgReplyDraft("batch").comment) || "";
    const hasBatchDraft = Boolean(initialBatchComment && initialBatchComment.trim());

    return `<div id="batchModeSection" class="pg-batch-view commit-section batch-mode-section">
      <div id="batchVoteHeaderStrip" class="pg-batch-header batch-mode-banner">
        <div class="pg-batch-header-left batch-mode-banner-left">
          ${renderExpandSidebarButtonHtml()}
          <span id="voteTargetBadge" class="pg-batch-badge vote-target-pill is-batch">
            <span class="pg-batch-badge-icon" aria-hidden="true">☑</span>
            <span>Batch Review · <strong>${count}</strong> change${count === 1 ? "" : "s"} selected</span>
            <button type="button" class="vote-target-clear" onclick="event.stopPropagation(); clearCheckedCls()" title="Clear checked CLs">✕</button>
          </span>
          <span class="pg-batch-sub batch-mode-sub">Review comments and label votes apply across all checked changes (<code>--notify=OWNER</code>).</span>
        </div>
        <div class="pg-batch-header-actions">
          ${renderBatchSubmitButtonHtml(checkedNums)}
          <button id="btnExitBatchMode" type="button" class="pg-btn-flat btn btn-exit-batch pg-batch-btn-exit" onclick="clearCheckedCls()" title="Uncheck all commits and return to code diff viewer (Esc)">✕ Exit Batch Mode</button>
        </div>
      </div>

      <div class="pg-batch-card pg-batch-changes-card">
        <div class="pg-batch-card-header">
          <div class="pg-batch-card-header-left">
            <span class="pg-batch-card-title">Selected Changes (<span id="pgBatchSelectedCount">${count}</span>)</span>
            ${deltaSummary}
            <span class="pg-batch-card-subtitle">Click 'Diff ↗' to inspect an individual change</span>
          </div>
          <div class="pg-batch-card-header-right">
            ${state.changes.length > count ? `<button type="button" class="pg-btn-flat pg-batch-btn-select-all" style="font-size:11.5px;padding:3px 8px;margin-right:4px;" onclick="selectAllBatchCls()" title="Select all ${state.changes.length} open changes in dashboard">Select all (${state.changes.length})</button>` : ""}
            <button type="button" class="pg-btn-flat pg-batch-btn-deselect-all" style="font-size:11.5px;padding:3px 8px;" onclick="clearCheckedCls()" title="Uncheck all changes">Deselect all</button>
          </div>
        </div>
        <div class="pg-batch-changes-list">
          ${tableHeaderHtml}
          ${changesRowsHtml}
        </div>
        <div class="batch-selected-cl-list" style="display:none;">${chipsHtml}</div>
      </div>

      <div class="pg-batch-card pg-batch-reviewers-card">
        <div class="pg-batch-card-header">
          <div class="pg-batch-card-header-left">
            <span class="pg-batch-card-title">Batch Reviewers & CC</span>
            <span class="pg-batch-card-subtitle">Add reviewers or CC across all ${count} checked changes simultaneously (notify: OWNER)</span>
          </div>
        </div>
        <div class="pg-batch-card-body" style="padding:10px 16px;">
          <div id="batchParticipantActions" class="batch-participant-actions" onclick="event.stopPropagation()">
            <button id="btnBatchAddReviewer" type="button" class="pg-btn-flat btn btn-batch-participant pg-batch-btn-participant" onclick="openBatchReviewerComposer('REVIEWER')" title="Add a Reviewer across all ${count} checked CLs (notify: OWNER)">+ Add Reviewer (${count})</button>
            <button id="btnBatchAddCc" type="button" class="pg-btn-flat btn btn-batch-participant pg-batch-btn-participant" onclick="openBatchReviewerComposer('CC')" title="Add a CC across all ${count} checked CLs (notify: OWNER)">+ Add CC (${count})</button>
            <div id="batchReviewerPopover" class="batch-reviewer-popover" style="display:none;"></div>
          </div>
        </div>
      </div>

      <div id="stagedVoteBar" class="pg-batch-card pg-batch-reply-card pg-reply-dialog pg-batch-vote-panel gerrit-vote-panel" role="region" aria-label="Batch vote and reply for ${count} changes" onclick="event.stopPropagation()">
        <div class="pg-reply-head">
          <span>Reply and vote on <strong>${count} change${count === 1 ? "" : "s"}</strong> <span class="pg-batch-reply-subhead">(Batch Vote & Reply)</span></span>
          <span class="pg-reply-head-sub">Acting as ${escapeHtml(state.viewer || "viewer")}</span>
        </div>
        <div class="pg-reply-section pg-reply-quick-section" style="padding:10px 16px;">
          <div class="pg-reply-quick-header">
            <span class="pg-reply-quick-title">Quick responses</span>
            <span class="pg-reply-quick-hint">Click preset or press Alt+L</span>
          </div>
          <div class="pg-reply-quick-chips" role="toolbar" aria-label="Quick reply presets">
            ${renderPgReplyQuickChipsHtml()}
          </div>
        </div>
        <div class="pg-reply-section pg-reply-message-section" style="padding:12px 16px 14px 16px;">
          <div class="pg-reply-message-head">
            <div class="pg-reply-message-head-left">
              <span class="pg-reply-message-title">Review Message across ${count} changes</span>
              <div class="pg-reply-message-tabs" role="tablist" aria-label="Review message editor mode">
                <button type="button" id="btnReplyTabEdit" class="pg-reply-tab is-active" role="tab" aria-selected="true" onclick="setPgReplyMessageTab('edit', 'batch')" title="Edit review message (Shortcut: Alt+P)">✎ Edit</button>
                <button type="button" id="btnReplyTabPreview" class="pg-reply-tab" role="tab" aria-selected="false" onclick="setPgReplyMessageTab('preview', 'batch')" title="Preview formatted markdown message (Shortcut: Alt+P)">👁 Preview</button>
              </div>
            </div>
            <div class="pg-reply-message-actions">
              <span id="replyDialogAutosaveBadge" class="pg-draft-autosave-badge is-saved" title="Draft saved automatically">✓ Saved</span>
              <button type="button" id="btnDiscardReplyCommentDraft" class="pg-btn-subtle pg-btn-discard-reply-draft" style="${hasBatchDraft ? "" : "display:none;"}" onclick="discardReplyCommentDraft('batch')" title="Discard draft review message">Discard</button>
            </div>
          </div>
          ${renderPgReplyMarkdownToolbarHtml('batch')}
          <textarea
            id="stagedVoteCommentInput"
            class="pg-reply-textarea staged-vote-note-input"
            rows="3"
            placeholder="Say something nice across all ${count} changes… (Ctrl+Enter to send · Esc to exit · Alt+L for LGTM +2 · Markdown supported)"
            oninput="onStagedVoteCommentInput(this.value)"
            onblur="flushPgReplyDialogDraftAutosave('batch')"
            onkeydown="onStagedVoteCommentKeydown(event)"
          >${escapeHtml(initialBatchComment)}</textarea>
          <div id="pgReplyMessagePreview" class="pg-reply-message-preview" style="display:none;" aria-label="Review message markdown preview"></div>
        </div>
        <div class="pg-reply-section pg-reply-labels-section" style="padding:12px 16px;">
          ${renderPgLabelScoresTableHtml({ isBatch: true, count, checkedNums })}
          <button id="btnToggleVoteNote" type="button" class="btn btn-vote-note-toggle" style="display:none;" onclick="toggleStagedVoteNote()">💬 Comment</button>
          <div id="stagedVoteNoteRow" style="display:none;"></div>
        </div>
        <div class="pg-reply-footer">
          <span class="pg-reply-attn-note">Applying across ${count} checked change${count === 1 ? "" : "s"} · Scoped to OWNER (--notify=OWNER)</span>
          <div class="pg-reply-footer-btns">
            <button id="btnResetStagedVotes" type="button" class="pg-btn-flat btn btn-reset-staged pg-batch-btn-reset" style="display:none;" onclick="resetStagedVote()" title="Discard unsent vote selections">Reset</button>
            <button id="btnCancelBatchReply" type="button" class="pg-btn-flat pg-batch-btn-cancel" onclick="clearCheckedCls()" title="Cancel batch mode and return to diff (Esc)">Cancel</button>
            <button id="btnSubmitVotes" type="button" class="pg-btn-primary btn-submit-votes pg-batch-btn-submit" onclick="submitStagedVotes(this)" disabled title="Send batch reply and votes across all ${count} changes (Ctrl+Enter / Enter)">Send</button>
          </div>
        </div>
      </div>
    </div>`;
  }

  function renderPgVoteBoxChipHtml(whoObj, val, labelName) {
    const name = whoObj.name || whoObj.short || whoObj.email || "user";
    const avatarBg = whoObj.is_bot ? "#5f6368" : computePgAvatarColor(whoObj.email || name);
    const initial = computePgInitial(whoObj);
    return window.AemuGerritReact.renderPgVoteBoxChipHtml(whoObj, val, labelName, avatarBg, initial);
  }

  function collectCrVoterEntriesForSr(c) {
    const seen = new Set();
    const entries = [];
    const addVoter = (who, val) => {
      const v = Number(val || 0);
      if (!v) return;
      const key = String(who.email || who.short || who.name || "").toLowerCase();
      if (!key || seen.has(key)) return;
      seen.add(key);
      entries.push({ who, val: v });
    };
    getFallbackReviewersForCl(c).filter(p => !p.is_bot).forEach(p => addVoter(p, p.cr_vote));
    (c.human_cr2 || []).forEach(v => addVoter(v, 2));
    (c.human_cr1 || []).forEach(v => addVoter(v, 1));
    entries.sort((a, b) => {
      if (a.val < 0 && b.val >= 0) return -1;
      if (b.val < 0 && a.val >= 0) return 1;
      return b.val - a.val;
    });
    return entries;
  }

  function renderPgCrSubmitRequirementRowHtml(c) {
    const voterEntries = collectCrVoterEntriesForSr(c);
    let minVote = 0;
    voterEntries.forEach(e => {
      if (e.val < minVote) minVote = e.val;
    });
    const reqCr2 = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
    const humanCr2Len = Array.isArray(c.human_cr2) ? c.human_cr2.length : 0;
    const doneCr2 = Math.max(humanCr2Len, Number(c.human_cr2_count || 0));
    const satisfied = minVote > -2 && doneCr2 >= reqCr2 && reqCr2 > 0;
    const chipsHtml = voterEntries.map(e => renderPgVoteBoxChipHtml(e.who, e.val, "Code-Review")).join("");
    return window.AemuGerritReact.renderPgCrSubmitRequirementRowHtml({
      satisfied,
      minVote,
      reqCr2,
      doneCr2,
      chipsHtml,
    });
  }

  function renderPgPvSubmitRequirementRowHtml(c) {
    const pv = getClPresubmitDisplay(c);
    const score = Number(c.presubmit_score || 0);
    const satisfied = Boolean(c.presubmit_vetted || score >= 2 || pv.status === "passed");
    const running = !satisfied && (pv.status === "running" || pv.status === "queued");
    let votesHtml = `<span class="pg-no-votes">No votes</span>`;
    if (score !== 0) {
      votesHtml = renderPgVoteBoxChipHtml({ name: "TreeHugger", short: "TH", is_bot: true }, score, "Presubmit-Verified");
    } else if (running) {
      votesHtml = `<span class="pg-sr-hint">${escapeHtml(pv.cleanLabel || pv.label)}</span>`;
    }
    return window.AemuGerritReact.renderPgPvSubmitRequirementRowHtml({
      clNum: Number(c.number),
      satisfied,
      running,
      tooltip: pv.tooltip || "",
      votesHtml,
    });
  }

  function renderPgTriggerVotesSectionHtml(c) {
    const prVoters = Array.isArray(c.presubmit_ready_voters) ? c.presubmit_ready_voters : [];
    const asVoters = Array.isArray(c.autosubmit_voters) ? c.autosubmit_voters : [];
    const prVotesHtml = prVoters.length > 0
      ? prVoters.map(v => renderPgVoteBoxChipHtml({ name: v, short: v, email: v }, 1, "Presubmit-Ready")).join("")
      : `<span class="pg-no-votes">No votes</span>`;
    const asVotesHtml = asVoters.length > 0
      ? asVoters.map(v => renderPgVoteBoxChipHtml({ name: v, short: v, email: v }, 1, "Autosubmit")).join("")
      : `<span class="pg-no-votes">No votes</span>`;

    const extraByLabel = new Map();
    getFallbackReviewersForCl(c).forEach(p => {
      (p.votes || []).forEach(vt => {
        const lbl = vt.label || "";
        if (!lbl || lbl === "Code-Review" || lbl === "Presubmit-Verified" || lbl === "Presubmit-Ready" || lbl === "Autosubmit") {
          return;
        }
        if (!Number(vt.value || 0)) return;
        if (!extraByLabel.has(lbl)) extraByLabel.set(lbl, []);
        extraByLabel.get(lbl).push(renderPgVoteBoxChipHtml(p, vt.value, lbl));
      });
    });
    const extraPills = Array.from(extraByLabel.entries()).map(
      ([lbl, chips]) => ({ label: lbl, chipsHtml: chips.join("") })
    );
    return window.AemuGerritReact.renderPgTriggerVotesSectionHtml({
      prVotesHtml,
      asVotesHtml,
      extraPills,
    });
  }

  function isClCrSatisfied(c) {
    if (!c) return false;
    const voterEntries = collectCrVoterEntriesForSr(c);
    let minVote = 0;
    voterEntries.forEach(e => {
      if (e.val < minVote) minVote = e.val;
    });
    const reqCr2 = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
    const humanCr2Len = Array.isArray(c.human_cr2) ? c.human_cr2.length : 0;
    const doneCr2 = Math.max(humanCr2Len, Number(c.human_cr2_count || 0));
    return minVote > -2 && doneCr2 >= reqCr2 && reqCr2 > 0;
  }

  function isPgSrExpanded(clNum, reqName) {
    const num = Number(clNum);
    return Boolean(state.expandedSrByCl && state.expandedSrByCl[num] && state.expandedSrByCl[num].has(reqName));
  }

  function isPgSrNaExpanded(clNum) {
    const num = Number(clNum);
    return Boolean(state.expandedSrNaByCl && state.expandedSrNaByCl[num]);
  }

  function togglePgSrNaList(clNum) {
    const num = Number(clNum);
    if (!state.expandedSrNaByCl) state.expandedSrNaByCl = {};
    state.expandedSrNaByCl[num] = !state.expandedSrNaByCl[num];
    const slot = document.getElementById(`pgSrSlot-${num}`);
    const clObj = (state.changes || []).find(x => Number(x.number) === num);
    if (slot && clObj) {
      slot.innerHTML = renderPgSubmitRequirementsHtml(clObj);
    }
  }

  function togglePgSubmitRequirementDetails(clNum, reqName) {
    const num = Number(clNum);
    if (!state.expandedSrByCl) state.expandedSrByCl = {};
    if (!state.expandedSrByCl[num]) state.expandedSrByCl[num] = new Set();
    const set = state.expandedSrByCl[num];
    if (set.has(reqName)) {
      set.delete(reqName);
    } else {
      set.add(reqName);
    }
    const slot = document.getElementById(`pgSrSlot-${num}`);
    const clObj = (state.changes || []).find(x => Number(x.number) === num);
    if (slot && clObj) {
      slot.innerHTML = renderPgSubmitRequirementsHtml(clObj);
    }
  }

  function renderPgSrAtomsHtml(sr) {
    return window.AemuGerritReact.renderPgSrAtomsHtml(sr);
  }

  function renderPgSrActionBtnHtml(c, sr) {
    return window.AemuGerritReact.renderPgSrActionBtnHtml(c, sr);
  }

  function renderPgSrDetailRowHtml(c, sr) {
    return window.AemuGerritReact.renderPgSrDetailRowHtml(c, sr);
  }

  function renderPgSubmitRequirementsHtml(c) {
    if (!c) return "";
    const srList = Array.isArray(c.submit_requirements) && c.submit_requirements.length > 0
      ? c.submit_requirements
      : null;

    let satisfiedCount = 0;
    let totalCount = 0;
    let rowsHtml = "";

    if (srList) {
      const applicable = srList.filter(r => (r.status || "").toUpperCase() !== "NOT_APPLICABLE");
      const notApplicable = srList.filter(r => (r.status || "").toUpperCase() === "NOT_APPLICABLE");
      totalCount = applicable.length;
      satisfiedCount = applicable.filter(r => {
        const s = (r.status || "").toUpperCase();
        return s === "SATISFIED" || s === "OVERRIDDEN";
      }).length;

      const isNaOpen = isPgSrNaExpanded(c.number);
      function renderSingleSrRow(sr, isNa, isVisible = true) {
        const name = sr.name || "Requirement";
        const status = (sr.status || "UNSATISFIED").toUpperCase();
        const isExp = isPgSrExpanded(c.number, name);
        let iconHtml = `<span class="pg-sr-icon sr-unsat" title="${escapeHtml(sr.description || status)}">✕</span>`;
        if (status === "SATISFIED") {
          iconHtml = `<span class="pg-sr-icon sr-ok" title="Satisfied">✓</span>`;
        } else if (status === "OVERRIDDEN") {
          iconHtml = `<span class="pg-sr-icon sr-overridden" title="Overridden">↷</span>`;
        } else if (status === "BLOCKED") {
          iconHtml = `<span class="pg-sr-icon sr-unsat sr-blocked" title="Blocked">✕</span>`;
        } else if (status === "RUNNING") {
          iconHtml = `<span class="pg-sr-icon sr-warn" title="Running">◷</span>`;
        } else if (status === "NOT_APPLICABLE") {
          iconHtml = `<span class="pg-sr-icon sr-na" title="Not applicable">—</span>`;
        }

        let votesHtml = `<span class="pg-no-votes">No votes</span>`;
        if (name === "Change-Is-Mergeable" || name === "Mergeable" || (status !== "SATISFIED" && (String(sr.description || "").toLowerCase().includes("conflict") || String(sr.expression || "").toLowerCase().includes("mergeable")))) {
          votesHtml = status === "SATISFIED"
            ? `<span class="pg-sr-hint" style="color:var(--g-green);font-weight:600;">Mergeable</span>`
            : `<span class="pg-sr-hint pg-sr-conflict-hint" style="color:var(--g-red,#d93025);font-weight:600;">Merge conflict</span>`;
        } else if (name === "Code-Review") {
          const voterEntries = collectCrVoterEntriesForSr(c);
          const chips = voterEntries.map(e => renderPgVoteBoxChipHtml(e.who, e.val, "Code-Review")).join("");
          const reqCr2 = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
          const humanCr2Len = Array.isArray(c.human_cr2) ? c.human_cr2.length : 0;
          const doneCr2 = Math.max(humanCr2Len, Number(c.human_cr2_count || 0));
          let hint = "";
          if (doneCr2 < reqCr2 && reqCr2 >= 2) {
            hint = `<span class="pg-sr-hint">${doneCr2 === 0 ? `Requires ${reqCr2} votes` : `${reqCr2 - doneCr2} more +2 needed`}</span>`;
          }
          votesHtml = chips ? `${chips}${hint}` : (hint || `<span class="pg-no-votes">No votes</span>`);
        } else if (name === "Presubmit-Verified") {
          const pv = getClPresubmitDisplay(c);
          const score = Number(c.presubmit_score || 0);
          if (score !== 0) {
            votesHtml = renderPgVoteBoxChipHtml({ name: "TreeHugger", short: "TH", is_bot: true }, score, "Presubmit-Verified");
          } else if (pv.status === "running" || pv.status === "queued") {
            votesHtml = `<span class="pg-sr-hint">${escapeHtml(pv.cleanLabel || pv.label)}</span>`;
          } else if (sr.status === "SATISFIED") {
            votesHtml = `<span class="pg-sr-hint" style="color:var(--g-green);font-weight:600;">Verified</span>`;
          }
        } else if (name === "Verified") {
          const vChips = [];
          getFallbackReviewersForCl(c).forEach(p => {
            (p.votes || []).forEach(vt => {
              if (vt.label === "Verified" && Number(vt.value || 0)) {
                vChips.push(renderPgVoteBoxChipHtml(p, vt.value, "Verified"));
              }
            });
          });
          votesHtml = vChips.length > 0 ? vChips.join("") : `<span class="pg-no-votes">No votes</span>`;
        } else if (sr.status === "SATISFIED") {
          votesHtml = `<span class="pg-sr-hint" style="color:var(--g-green);font-weight:600;">Satisfied</span>`;
        }

        const chevron = isExp ? "▼" : "▶";
        const detailHtml = isExp ? renderPgSrDetailRowHtml(c, sr) : "";
        const naCls = isNa ? " pg-sr-na-row" : "";
        const naStyle = isNa && !isVisible ? ' style="display:none;"' : "";

        return `<tr class="pg-sr-row${naCls} ${isExp ? "is-expanded" : ""}" data-cl="${c.number}" data-sr-name="${escapeHtml(name)}"${naStyle} onclick="event.stopPropagation(); togglePgSubmitRequirementDetails(${c.number}, '${escapeHtml(name)}')" title="Click to view ${escapeHtml(name)} requirement details">
          <td class="pg-sr-icon-cell">${iconHtml}</td>
          <td class="pg-sr-name">${escapeHtml(name)}</td>
          <td class="pg-sr-votes">${votesHtml}</td>
          <td class="pg-sr-expand-chevron">${chevron}</td>
        </tr>${detailHtml}`;
      }

      const appRowsHtml = applicable.map(sr => renderSingleSrRow(sr, false, true)).join("");
      const naRowsHtml = notApplicable.map(sr => renderSingleSrRow(sr, true, isNaOpen)).join("");
      const naToggleRowHtml = notApplicable.length > 0
        ? `<tr class="pg-sr-na-toggle-row">
            <td colspan="4" class="pg-sr-na-toggle-cell">
              <button id="btnTogglePgSrNa-${c.number}" type="button" class="pg-btn-subtle pg-sr-na-toggle-btn" onclick="event.stopPropagation(); togglePgSrNaList(${c.number})" title="Toggle non-applicable requirements">
                ${isNaOpen ? `Hide ${notApplicable.length} not applicable requirement${notApplicable.length === 1 ? "" : "s"} ▴` : `Show ${notApplicable.length} not applicable requirement${notApplicable.length === 1 ? "" : "s"} ▾`}
              </button>
            </td>
          </tr>`
        : "";
      rowsHtml = `${appRowsHtml}${naToggleRowHtml}${naRowsHtml}`;
    } else {
      // Fallback to Code-Review + Presubmit-Verified
      const crSat = isClCrSatisfied(c);
      const pv = getClPresubmitDisplay(c);
      const pvScore = Number(c.presubmit_score || 0);
      const pvSat = Boolean(c.presubmit_vetted || pvScore >= 2 || pv.status === "passed");
      totalCount = 2;
      satisfiedCount = (crSat ? 1 : 0) + (pvSat ? 1 : 0);
      rowsHtml = `${renderPgCrSubmitRequirementRowHtml(c)}${renderPgPvSubmitRequirementRowHtml(c)}`;
    }

    return window.AemuGerritReact.renderPgSubmitRequirementsSectionHtml({
      clNum: Number(c.number),
      satisfiedCount,
      totalCount,
      rowsHtml,
      triggerVotesHtml: renderPgTriggerVotesSectionHtml(c),
    });
  }

  function highlightPgSrExpression(expr) {
    if (!expr) return "";
    const safe = escapeHtml(String(expr));
    return safe
      .replace(/\b(AND|OR|NOT)\b/g, '<span class="pg-expr-op">$1</span>')
      .replace(/\b(label|branch|change|is|has|project|owner|uploader|commit):/g, '<span class="pg-expr-key">$1:</span>');
  }

  function renderPgSrAuditCardHtml(c, sr) {
    const name = (sr && sr.name) || "Requirement";
    let crChipsHtml = "";
    if (name === "Code-Review" && c) {
      const voterEntries = collectCrVoterEntriesForSr(c);
      crChipsHtml = voterEntries.map(e => renderPgVoteBoxChipHtml(e.who, e.val, "Code-Review")).join("");
    }
    return window.AemuGerritReact.renderPgSrAuditCardHtml({
      clNum: Number((c && c.number) || 0),
      sr: sr || {},
      highlightedExprHtml: sr && sr.expression ? highlightPgSrExpression(sr.expression) : "",
      highlightedAppHtml: sr && sr.applicability_expression ? highlightPgSrExpression(sr.applicability_expression) : "",
      highlightedOvrHtml: sr && sr.override_expression ? highlightPgSrExpression(sr.override_expression) : "",
      crChipsHtml,
    });
  }

  function renderPgSrAuditContent(c) {
    if (!c) {
      const num = Number(state.srAuditCl || state.selectedClNumber);
      c = (state.changes || []).find(x => Number(x.number) === num);
    }
    if (!c) return;

    const summary = computePgSubmitRequirementsSummary(c);
    const heroEl = document.getElementById("pgSrAuditHeroBanner");
    if (heroEl) {
      let heroCls = "is-pending";
      let heroIcon = "✕";
      let heroTitle = "✕ Not Submittable";
      let heroSub = `${summary.satisfied} of ${summary.total} requirements satisfied. Needs additional votes or approvals.`;
      if (summary.status === "submittable") {
        heroCls = "is-submittable";
        heroIcon = "✓";
        heroTitle = "✓ Ready to Submit";
        heroSub = `All ${summary.total} submit requirements are satisfied. This change can be submitted.`;
      } else if (summary.status === "blocked") {
        heroCls = "is-blocked";
        heroIcon = "✕";
        heroTitle = "✕ Submission Blocked";
        heroSub = summary.tip || "Blocked by a negative review vote (-2) or failing check.";
      }
      heroEl.className = `pg-sr-audit-hero ${heroCls}`;
      heroEl.innerHTML = `
        <div class="pg-sr-hero-icon" aria-hidden="true">${heroIcon}</div>
        <div class="pg-sr-hero-text">
          <div class="pg-sr-hero-title">${escapeHtml(heroTitle)}</div>
          <div class="pg-sr-hero-sub">${escapeHtml(heroSub)}</div>
        </div>
      `;
    }

    const cardsContainer = document.getElementById("pgSrAuditCardsContainer");
    const rawList = Array.isArray(c.submit_requirements) && c.submit_requirements.length > 0
      ? c.submit_requirements
      : [];
    let srs = rawList.length > 0 ? rawList : [
      {
        name: "Code-Review",
        status: isClCrSatisfied(c) ? "SATISFIED" : "UNSATISFIED",
        description: "Requires code review approval and no blocking votes",
        expression: "label:Code-Review=MAX AND -label:Code-Review=MIN",
        passing_atoms: isClCrSatisfied(c) ? ["Code-Review +2"] : [],
        failing_atoms: isClCrSatisfied(c) ? [] : ["Needs human Code-Review +2"],
      },
      {
        name: "Presubmit-Verified",
        status: getClPresubmitDisplay(c).status === "passed" ? "SATISFIED" : "UNSATISFIED",
        description: "Presubmit test verification",
        expression: "label:Presubmit-Verified=+2",
        passing_atoms: getClPresubmitDisplay(c).status === "passed" ? ["Presubmit-Verified +2"] : [],
        failing_atoms: getClPresubmitDisplay(c).status === "passed" ? [] : ["Presubmit verification pending"],
      }
    ];

    const q = String(state.srAuditSearchQuery || "").trim().toLowerCase();
    const opFilter = state.srAuditFilter || "all";

    let visibleCards = srs.filter(sr => {
      const st = (sr.status || "UNSATISFIED").toUpperCase();
      const isSat = st === "SATISFIED" || st === "OVERRIDDEN";
      if (opFilter === "satisfied" && !isSat) return false;
      if (opFilter === "unsatisfied" && isSat) return false;

      if (q) {
        const text = `${sr.name || ""} ${sr.description || ""} ${sr.expression || ""} ${(sr.passing_atoms || []).join(" ")} ${(sr.failing_atoms || []).join(" ")}`.toLowerCase();
        if (!text.includes(q)) return false;
      }
      return true;
    });

    if (cardsContainer) {
      if (visibleCards.length === 0) {
        cardsContainer.innerHTML = `<div class="pg-empty-state" style="padding:24px;text-align:center;color:var(--g-muted);">
          <span>No submit requirements match the current filter.</span>
        </div>`;
      } else {
        cardsContainer.innerHTML = visibleCards.map(sr => renderPgSrAuditCardHtml(c, sr)).join("");
      }
    }

    const triggerContainer = document.getElementById("pgSrAuditTriggerContainer");
    if (triggerContainer) {
      const prVoters = Array.isArray(c.presubmit_ready_voters) ? c.presubmit_ready_voters : [];
      const asVoters = Array.isArray(c.autosubmit_voters) ? c.autosubmit_voters : [];
      const hasTriggers = prVoters.length > 0 || asVoters.length > 0 || (Array.isArray(c.trigger_votes) && c.trigger_votes.length > 0);
      if (hasTriggers) {
        triggerContainer.style.display = "block";
        triggerContainer.innerHTML = `
          <div class="pg-sr-trigger-title">Non-blocking / Trigger Votes</div>
          <div class="pg-sr-trigger-grid">
            <div class="pg-meta-pill">Presubmit-Ready: ${prVoters.length > 0 ? `+1 (${prVoters.map(escapeHtml).join(", ")})` : "0"}</div>
            <div class="pg-meta-pill">Autosubmit: ${asVoters.length > 0 ? `+1 (${asVoters.map(escapeHtml).join(", ")})` : "0"}</div>
          </div>
        `;
      } else {
        triggerContainer.style.display = "none";
      }
    }

    const footerStats = document.getElementById("pgSrAuditFooterStats");
    if (footerStats) {
      const satCount = srs.filter(sr => {
        const st = (sr.status || "").toUpperCase();
        return st === "SATISFIED" || st === "OVERRIDDEN";
      }).length;
      footerStats.textContent = `Showing ${visibleCards.length} of ${srs.length} requirements (${satCount} satisfied)`;
    }

    const filterBtns = document.querySelectorAll(".pg-sr-status-filter .pg-segmented-btn");
    filterBtns.forEach(btn => {
      const f = btn.getAttribute("data-sr-filter");
      btn.classList.toggle("is-active", f === opFilter);
    });
  }

  function openPgSubmitRequirementsAuditDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = (state.changes || []).find(x => Number(x.number) === num);
    if (!c) return;
    state.srAuditDialogOpen = true;
    state.srAuditCl = num;
    if (!state.srAuditFilter) state.srAuditFilter = "all";
    if (state.srAuditSearchQuery == null) state.srAuditSearchQuery = "";

    const backdrop = document.getElementById("pgSrAuditBackdrop");
    const subTitle = document.getElementById("pgSrAuditSubtitle");
    const searchInp = document.getElementById("pgSrAuditSearchInput");
    if (subTitle) {
      subTitle.textContent = `ag/${c.number}: ${c.subject || ""} · Patchset ${c.patch_set || 1}`;
    }
    if (searchInp) {
      searchInp.value = state.srAuditSearchQuery;
      const clearBtn = document.getElementById("btnClearSrAuditFilter");
      if (clearBtn) clearBtn.style.display = state.srAuditSearchQuery ? "inline-block" : "none";
    }

    renderPgSrAuditContent(c);

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
  }

  function closePgSubmitRequirementsAuditDialog() {
    state.srAuditDialogOpen = false;
    const backdrop = document.getElementById("pgSrAuditBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function togglePgSubmitRequirementsAuditDialog(clNum) {
    if (state.srAuditDialogOpen) {
      closePgSubmitRequirementsAuditDialog();
    } else {
      openPgSubmitRequirementsAuditDialog(clNum);
    }
  }

  function onPgSrAuditBackdropClick(e) {
    if (e.target && e.target.id === "pgSrAuditBackdrop") {
      closePgSubmitRequirementsAuditDialog();
    }
  }

  function setPgSrAuditFilter(filterType) {
    state.srAuditFilter = filterType;
    renderPgSrAuditContent();
  }

  function onPgSrAuditFilterInput(e) {
    state.srAuditSearchQuery = (e && e.target && e.target.value) || "";
    const clearBtn = document.getElementById("btnClearSrAuditFilter");
    if (clearBtn) clearBtn.style.display = state.srAuditSearchQuery ? "inline-block" : "none";
    renderPgSrAuditContent();
  }

  function clearPgSrAuditFilter() {
    state.srAuditSearchQuery = "";
    const inp = document.getElementById("pgSrAuditSearchInput");
    if (inp) inp.value = "";
    const clearBtn = document.getElementById("btnClearSrAuditFilter");
    if (clearBtn) clearBtn.style.display = "none";
    renderPgSrAuditContent();
  }

  function copyPgSubmitRequirementsAuditReport(clNum) {
    const num = Number(clNum || state.srAuditCl || state.selectedClNumber);
    const c = (state.changes || []).find(x => Number(x.number) === num);
    if (!c) {
      showToast("No change selected for audit report", true);
      return;
    }
    const summary = computePgSubmitRequirementsSummary(c);
    const srs = Array.isArray(c.submit_requirements) && c.submit_requirements.length > 0
      ? c.submit_requirements
      : [];
    const triggerVotes = Array.isArray(c.trigger_votes) ? c.trigger_votes : [];

    let lines = [];
    lines.push(`### Submit Requirements Audit — ag/${c.number} (PS ${c.patch_set || 1})`);
    lines.push(`**Subject:** ${c.subject || ""}`);
    lines.push(`**Overall Status:** ${summary.label} (${summary.status})`);
    lines.push("");
    lines.push("| Requirement | Status | Submittability Expression | Evaluation / Atoms |");
    lines.push("| --- | --- | --- | --- |");

    if (srs.length > 0) {
      srs.forEach(sr => {
        const name = sr.name || "Requirement";
        const st = sr.status || "UNSATISFIED";
        const expr = (sr.expression || "N/A").replace(/\|/g, "\\|");
        const passing = Array.isArray(sr.passing_atoms) ? sr.passing_atoms.map(a => `✓ ${a}`) : [];
        const failing = Array.isArray(sr.failing_atoms) ? sr.failing_atoms.map(a => `✕ ${a}`) : [];
        const evalStr = [...passing, ...failing].join("<br>").replace(/\|/g, "\\|") || "N/A";
        lines.push(`| **${name}** | \`${st}\` | \`${expr}\` | ${evalStr} |`);
      });
    } else {
      lines.push(`| **Code-Review** | \`${isClCrSatisfied(c) ? "SATISFIED" : "UNSATISFIED"}\` | \`label:Code-Review=MAX\` | ${isClCrSatisfied(c) ? "✓ Approved (+2)" : "✕ Pending (+2)"} |`);
      const pv = getClPresubmitDisplay(c);
      lines.push(`| **Presubmit-Verified** | \`${pv.status === "passed" ? "SATISFIED" : "UNSATISFIED"}\` | \`label:Presubmit-Verified=+2\` | ${pv.status === "passed" ? "✓ Verified" : "✕ Pending"} |`);
    }

    if (triggerVotes.length > 0 || (Array.isArray(c.presubmit_ready_voters) && c.presubmit_ready_voters.length > 0) || (Array.isArray(c.autosubmit_voters) && c.autosubmit_voters.length > 0)) {
      lines.push("");
      lines.push("#### Trigger Votes & Non-blocking Labels");
      const prVoters = Array.isArray(c.presubmit_ready_voters) ? c.presubmit_ready_voters.join(", ") : "";
      const asVoters = Array.isArray(c.autosubmit_voters) ? c.autosubmit_voters.join(", ") : "";
      if (prVoters) lines.push(`- **Presubmit-Ready:** +1 (${prVoters})`);
      if (asVoters) lines.push(`- **Autosubmit:** +1 (${asVoters})`);
    }

    lines.push("");
    lines.push("_Generated by AEMU Fast Gerrit (PolyGerrit)_");

    const reportText = lines.join("\n");
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(reportText).then(() => {
        showToast("Submit requirements audit report copied to clipboard! 📋");
      }).catch(() => {
        showToast("Submit requirements audit report generated! 📋");
      });
    } else {
      showToast("Submit requirements audit report generated! 📋");
      return Promise.resolve();
    }
  }

  if (typeof window !== "undefined") {
    window.openPgSubmitRequirementsAuditDialog = openPgSubmitRequirementsAuditDialog;
    window.closePgSubmitRequirementsAuditDialog = closePgSubmitRequirementsAuditDialog;
    window.togglePgSubmitRequirementsAuditDialog = togglePgSubmitRequirementsAuditDialog;
    window.onPgSrAuditBackdropClick = onPgSrAuditBackdropClick;
    window.setPgSrAuditFilter = setPgSrAuditFilter;
    window.onPgSrAuditFilterInput = onPgSrAuditFilterInput;
    window.clearPgSrAuditFilter = clearPgSrAuditFilter;
    window.copyPgSubmitRequirementsAuditReport = copyPgSubmitRequirementsAuditReport;
    window.highlightPgSrExpression = highlightPgSrExpression;
    window.renderPgSrAuditContent = renderPgSrAuditContent;
    window.renderPgSrAuditCardHtml = renderPgSrAuditCardHtml;
  }

