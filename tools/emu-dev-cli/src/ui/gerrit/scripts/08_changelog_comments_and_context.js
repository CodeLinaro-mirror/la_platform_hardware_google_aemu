  function formatPgRelativeDate(rawDate) {
    if (!rawDate) return "";
    let s = String(rawDate).trim();
    if (s.includes(" ")) {
      s = s.replace(" ", "T");
    }
    if (s.includes(".")) {
      s = s.split(".")[0];
    }
    if (!s.endsWith("Z") && !/[+-]\d{2}:?\d{2}$/.test(s)) {
      s += "Z";
    }
    const d = new Date(s);
    if (isNaN(d.getTime())) return String(rawDate);
    const now = Date.now();
    const diffSec = Math.max(0, Math.floor((now - d.getTime()) / 1000));
    if (diffSec < 60) return "just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const yr = d.getUTCFullYear();
    const curYr = new Date().getUTCFullYear();
    return `${months[d.getUTCMonth()]} ${d.getUTCDate()}${yr !== curYr ? `, ${yr}` : ""}`;
  }

  function getPgAuthorInitials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return Array.from(parts[0])[0].toUpperCase();
    const first = Array.from(parts[0])[0];
    const last = Array.from(parts[parts.length - 1])[0];
    return (first + last).toUpperCase();
  }

  function formatPgMessageContentHtml(rawText) {
    if (!rawText) return `<span style="color:var(--g-muted);font-style:italic;">(Empty message)</span>`;
    const lines = String(rawText).split("\n");
    const blocks = [];
    let curQuote = [];
    function flushQuote() {
      if (curQuote.length > 0) {
        blocks.push(`<blockquote class="pg-msg-quote">${curQuote.join("<br/>")}</blockquote>`);
        curQuote = [];
      }
    }
    for (const line of lines) {
      if (line.trimStart().startsWith(">")) {
        const qText = line.replace(/^\s*>\s?/, "");
        curQuote.push(renderInlineMarkdownHtml(qText, false));
      } else {
        flushQuote();
        let formatted = renderInlineMarkdownHtml(line, false);
        formatted = formatted.replace(/\bCode-Review\s*\+2\b/g, `<span class="pg-vote-chip vote-cr2">Code-Review +2</span>`);
        formatted = formatted.replace(/\bCode-Review\s*\+1\b/g, `<span class="pg-vote-chip vote-cr1">Code-Review +1</span>`);
        formatted = formatted.replace(/\bCode-Review\s*-1\b/g, `<span class="pg-vote-chip vote-cr-neg1">Code-Review -1</span>`);
        formatted = formatted.replace(/\bCode-Review\s*-2\b/g, `<span class="pg-vote-chip vote-cr-neg2">Code-Review -2</span>`);
        formatted = formatted.replace(/\bPresubmit-Verified\s*\+1\b/g, `<span class="pg-vote-chip vote-pv1">Presubmit-Verified +1</span>`);
        formatted = formatted.replace(/\bPresubmit-Verified\s*-1\b/g, `<span class="pg-vote-chip vote-pv-neg1">Presubmit-Verified -1</span>`);
        formatted = formatted.replace(/\bAutosubmit\s*\+1\b/g, `<span class="pg-vote-chip vote-as1">Autosubmit +1</span>`);
        formatted = formatted.replace(/\bVerified\s*\+1\b/g, `<span class="pg-vote-chip vote-pv1">Verified +1</span>`);
        formatted = formatted.replace(/\bVerified\s*-1\b/g, `<span class="pg-vote-chip vote-pv-neg1">Verified -1</span>`);
        blocks.push(formatted);
      }
    }
    flushQuote();
    return blocks.join("<br/>");
  }

  function computePgMessageSummarySnippet(m, comments = []) {
    if (!m) return "";
    if (m.summary && typeof m.summary === "string" && m.summary.trim()) {
      return m.summary.trim();
    }

    const raw = String(m.message || "");
    const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean);

    const meaningfulLines = [];
    for (const line of lines) {
      if (line.startsWith(">")) continue;
      if (/^\(\d+\s+comments?\)$/i.test(line)) continue;

      const psMatch = line.match(/^(?:Patch\s*Set|Patchset)\s*\d+:\s*(.*)$/i);
      if (psMatch) {
        const remainder = psMatch[1].trim();
        if (!remainder) continue;
        const cleanedRemainder = remainder
          .replace(/\b(?:Code-Review|Presubmit-Verified|Verified|Autosubmit|Dr\.No|API-Review|Lint)\s*[+-]\d+\b/gi, "")
          .trim();
        if (!cleanedRemainder) continue;
        meaningfulLines.push(cleanedRemainder);
        continue;
      }

      if (/^Uploaded patch set \d+\.?$/i.test(line) || /^Commit Message was updated\.?$/i.test(line)) {
        continue;
      }

      const voteOnly = line.replace(/\b(?:Code-Review|Presubmit-Verified|Verified|Autosubmit|Dr\.No|API-Review|Lint)\s*[+-]\d+\b/gi, "").trim();
      if (!voteOnly) continue;

      meaningfulLines.push(line);
    }

    const hasOnlyFileHeaders = meaningfulLines.length > 0 && meaningfulLines.every(l => /^File\s+[^\s:]+:\s*$/i.test(l) || /^Line\s+\d+:/i.test(l));

    if (meaningfulLines.length > 0 && !hasOnlyFileHeaders) {
      const firstLine = meaningfulLines[0].replace(/\s+/g, " ").trim();
      return firstLine.length > 140 ? firstLine.substring(0, 137) + "..." : firstLine;
    }

    if (Array.isArray(comments) && comments.length > 0) {
      const first = comments[0];
      const fpath = first.file_path ? (first.file_path.split("/").pop() || first.file_path) : "inline";
      const fline = (first.line || first.start_line) ? `:${first.line || first.start_line}` : "";
      const commentMsg = String(first.latest_message || (first.messages && first.messages[0] && first.messages[0].message) || first.message || "").trim();
      const cleanComment = commentMsg.replace(/^\s*>\s?.*$/mg, "").replace(/\s+/g, " ").trim();
      const preview = cleanComment ? `"${cleanComment}"` : "Comment";
      const moreSuffix = comments.length > 1 ? ` (+${comments.length - 1} more)` : "";
      const fullStr = `[${fpath}${fline}] ${preview}${moreSuffix}`;
      return fullStr.length > 140 ? fullStr.substring(0, 137) + "..." : fullStr;
    }

    if (meaningfulLines.length > 0) {
      const firstLine = meaningfulLines[0].replace(/\s+/g, " ").trim();
      return firstLine.length > 140 ? firstLine.substring(0, 137) + "..." : firstLine;
    }

    const fallback = raw.replace(/\s+/g, " ").trim();
    return fallback.length > 140 ? fallback.substring(0, 137) + "..." : fallback;
  }

  function renderPgMessageCommentsSummaryHtml(comments) {
    if (!Array.isArray(comments) || comments.length === 0) return "";
    let unresCount = 0;
    let resCount = 0;
    for (const c of comments) {
      if (c && c.unresolved !== false) {
        unresCount++;
      } else {
        resCount++;
      }
    }
    const chips = [];
    if (unresCount > 0) {
      chips.push(`<span class="pg-msg-comments-summary pg-msg-summary-unresolved" title="${unresCount} unresolved comment thread${unresCount === 1 ? "" : "s"}"><span class="pg-msg-summary-icon" aria-hidden="true">💬</span> ${unresCount} unresolved</span>`);
    }
    if (resCount > 0) {
      chips.push(`<span class="pg-msg-comments-summary pg-msg-summary-resolved" title="${resCount} resolved comment thread${resCount === 1 ? "" : "s"}"><span class="pg-msg-summary-icon" aria-hidden="true">✓</span> ${resCount} resolved</span>`);
    }
    return chips.join("");
  }

  function togglePgMessageCard(event, clNum, msgIdx) {
    if (event && event.target && event.target.closest("a, button, select, input")) return;
    const card = document.getElementById(`pgMsg-${clNum}-${msgIdx}`);
    if (!card) return;
    const isExpanded = card.classList.contains("pg-msg-expanded");
    if (isExpanded) {
      card.classList.remove("pg-msg-expanded");
      card.classList.add("pg-msg-collapsed");
      const ch = card.querySelector(".pg-msg-chevron");
      if (ch) ch.textContent = "▶";
    } else {
      card.classList.remove("pg-msg-collapsed");
      card.classList.add("pg-msg-expanded");
      const ch = card.querySelector(".pg-msg-chevron");
      if (ch) ch.textContent = "▼";
    }
  }

  function expandAllPgMessages(clNum) {
    const section = document.getElementById(`pgChangeLog-${clNum}`);
    if (!section) return;
    section.querySelectorAll(".pg-message-card").forEach(card => {
      card.classList.remove("pg-msg-collapsed");
      card.classList.add("pg-msg-expanded");
      const ch = card.querySelector(".pg-msg-chevron");
      if (ch) ch.textContent = "▼";
    });
  }

  function collapseAllPgMessages(clNum) {
    const section = document.getElementById(`pgChangeLog-${clNum}`);
    if (!section) return;
    section.querySelectorAll(".pg-message-card").forEach(card => {
      card.classList.remove("pg-msg-expanded");
      card.classList.add("pg-msg-collapsed");
      const ch = card.querySelector(".pg-msg-chevron");
      if (ch) ch.textContent = "▶";
    });
  }

  function isPgMessageWithComments(m, clNum = null, bundle = null) {
    if (!m) return false;
    if (m.has_comments || (typeof m.comment_count === "number" && m.comment_count > 0) || (Array.isArray(m.comments) && m.comments.length > 0)) {
      return true;
    }
    const text = String(m.message || "");
    if (/\(\d+\s+comments?\)/i.test(text)) return true;
    if (/^\s*>/m.test(text)) return true;
    if (/\b(?:File|Line)\s+[^:\n]+:\s*\d+/i.test(text)) return true;
    if (clNum && typeof getPgCommentsForMessage === "function") {
      const matched = getPgCommentsForMessage(clNum, m, bundle);
      if (matched.length > 0) return true;
    }
    if (/\b(Code-Review|Presubmit-Verified|Verified|Autosubmit)\s*([+-]\d)\b/.test(text)) return true;
    if (!m.is_bot && !m.tag && text.trim().length > 0) return true;
    return false;
  }

  function isPgMessageWithVotes(m) {
    if (!m) return false;
    const text = String(m.message || "");
    if (/\b(Code-Review|Presubmit-Verified|Verified|Autosubmit)\s*([+-]\d)\b/i.test(text)) return true;
    if (m.tag && /vote/i.test(m.tag)) return true;
    if (m.labels && typeof m.labels === "object" && Object.keys(m.labels).length > 0) return true;
    return false;
  }

  function isPgMessageWithAttn(m) {
    if (!m) return false;
    const text = String(m.message || "");
    if (
      text.includes("attention set") ||
      text.includes("Attention set") ||
      text.includes("Added to attention set") ||
      text.includes("Removed from attention set")
    ) {
      return true;
    }
    if (m.tag && /attention/i.test(m.tag)) return true;
    return false;
  }

  function isPgMessageWithRobotComments(m, clNum = null, bundle = null) {
    if (!m) return false;
    if (m.has_robot_comments === true || m.is_robot_comments === true || m.has_robot === true || m.is_robot === true) return true;
    if (m.tag && /robot/i.test(m.tag)) return true;
    if (Array.isArray(m.comments) && m.comments.some(c => Boolean(c.is_robot || c.robot_id))) return true;
    if (clNum && typeof getPgCommentsForMessage === "function") {
      const matched = getPgCommentsForMessage(clNum, m, bundle);
      if (matched.some(c => Boolean(c.is_robot || c.robot_id))) return true;
    }
    const text = String(m.message || "");
    if (m.is_bot && /\b(?:clang-tidy|presubmit-bot|compiler-warning|linter|autotest)\b/i.test(text)) {
      return true;
    }
    if (/\b(?:clang-tidy|presubmit-bot|compiler-warning|linter|autotest)\b/i.test(text) && (isPgMessageWithComments(m, clNum, bundle) || /\b(?:File|Line)\s+/i.test(text))) {
      return true;
    }
    return false;
  }

  function getPgMessageCategoryFilters(clNum) {
    const num = Number(clNum || 0);
    if (!state.msgCategoryFiltersByCl) {
      state.msgCategoryFiltersByCl = {};
    }
    if (!state.msgCategoryFiltersByCl[num]) {
      state.msgCategoryFiltersByCl[num] = new Set();
    }
    return state.msgCategoryFiltersByCl[num];
  }

  function syncPgMessageFilterChipsDom(clNum) {
    const num = Number(clNum || 0);
    const container = document.getElementById(`pgMsgFilterChips-${num}`);
    if (!container || typeof container.querySelectorAll !== "function") return;
    const activeCats = getPgMessageCategoryFilters(num);
    const chips = container.querySelectorAll(".pg-msg-chip");
    chips.forEach(chip => {
      const cat = chip.getAttribute("data-cat");
      if (cat === "all") {
        const isAll = activeCats.size === 0;
        if (isAll) {
          chip.classList.add("is-active");
          chip.setAttribute("aria-pressed", "true");
        } else {
          chip.classList.remove("is-active");
          chip.setAttribute("aria-pressed", "false");
        }
      } else {
        const isActive = activeCats.has(cat);
        if (isActive) {
          chip.classList.add("is-active");
          chip.setAttribute("aria-pressed", "true");
        } else {
          chip.classList.remove("is-active");
          chip.setAttribute("aria-pressed", "false");
        }
      }
    });
  }

  function togglePgMessageCategoryFilter(clNum, category) {
    const num = Number(clNum || 0);
    const activeCats = getPgMessageCategoryFilters(num);
    const cat = String(category || "").toLowerCase();
    if (cat === "all") {
      activeCats.clear();
    } else if (activeCats.has(cat)) {
      activeCats.delete(cat);
    } else {
      activeCats.add(cat);
    }
    const cbComments = document.getElementById(`pgMsgFilterCommentsOnly-${num}`);
    if (cbComments) {
      cbComments.checked = activeCats.has("comments");
    }
    const cbAuto = document.getElementById(`pgMsgFilterAuto-${num}`);
    if (cbAuto && (cat === "automated" || cat === "robot") && activeCats.has(cat)) {
      cbAuto.checked = true;
      try {
        localStorage.setItem("pg_show_automated_messages", "1");
      } catch (_) {}
    }
    syncPgMessageFilterChipsDom(num);
    applyPgMessageFilters(num);
  }

  function toggleAllPgMessages(clNum) {
    const num = Number(clNum || 0);
    const section = document.getElementById(`pgChangeLog-${num}`);
    if (!section || typeof section.querySelectorAll !== "function") return;
    const cards = Array.from(section.querySelectorAll(".pg-message-card:not(.is-hidden)"));
    if (cards.length === 0) return;
    const hasCollapsed = cards.some(c => c.classList && c.classList.contains("pg-msg-collapsed"));
    if (hasCollapsed) {
      expandAllPgMessages(num);
      if (typeof showToast === "function") {
        showToast(`Expanded ${cards.length} messages`);
      }
    } else {
      collapseAllPgMessages(num);
      if (typeof showToast === "function") {
        showToast(`Collapsed ${cards.length} messages`);
      }
    }
  }

  function highlightPgSearchText(html, query) {
    if (!html || !query) return html;
    const trimmed = String(query).trim();
    if (!trimmed) return html;
    const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const regex = new RegExp(`(${escaped})`, "gi");
    return String(html).split(/(<[^>]+>)/g).map(chunk => {
      if (chunk.startsWith("<") && chunk.endsWith(">")) {
        return chunk;
      }
      return chunk.replace(regex, '<mark class="pg-msg-search-highlight">$1</mark>');
    }).join("");
  }

  function onPgMessageSearchInput(clNum, _val) {
    applyPgMessageFilters(clNum);
  }

  function clearPgMessageSearch(clNum) {
    const input = document.getElementById(`pgMsgFilterSearch-${clNum}`);
    if (input) {
      input.value = "";
      try { input.focus(); } catch (_) {}
    }
    applyPgMessageFilters(clNum);
  }

  function formatPgMessageTag(rawTag) {
    if (!rawTag) return "";
    const tag = String(rawTag).trim();
    if (!tag) return "";
    const knownTags = {
      "autogenerated:gerrit:newPatchSet": "New Patchset",
      "autogenerated:gerrit:setWorkInProgress": "Marked WIP",
      "autogenerated:gerrit:setReadyForReview": "Ready for Review",
      "autogenerated:gerrit:merged": "Merged",
      "autogenerated:gerrit:abandon": "Abandoned",
      "autogenerated:gerrit:restore": "Restored",
      "autogenerated:gerrit:cherryPick": "Cherry Pick",
      "autogenerated:gerrit:rebase": "Rebased",
      "autogenerated:gerrit:setHashtag": "Hashtags Updated",
      "autogenerated:gerrit:setTopic": "Topic Updated",
      "autogenerated:gerrit:setAssignee": "Assignee Updated",
      "autogenerated:gerrit:deleteReviewer": "Reviewer Removed",
      "autogenerated:gerrit:deleteVote": "Vote Removed",
    };
    if (knownTags[tag]) return knownTags[tag];
    let stripped = tag.replace(/^autogenerated:gerrit:/, "").replace(/^autogenerated:/, "");
    if (!stripped) return tag;
    stripped = stripped.replace(/[-_]+/g, " ");
    stripped = stripped.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
    return stripped.charAt(0).toUpperCase() + stripped.slice(1);
  }

  function onPgMessageTagFilterChange(clNum, _val) {
    applyPgMessageFilters(clNum);
  }

  function filterPgMessagesByTag(clNum, tag) {
    const num = Number(clNum || 0);
    const sel = document.getElementById(`pgMsgFilterTag-${num}`);
    if (sel) {
      sel.value = tag;
    }
    applyPgMessageFilters(num);
    const label = (typeof formatPgMessageTag === "function") ? formatPgMessageTag(tag) : tag;
    if (typeof showToast === "function") {
      showToast(`Filtered messages by tag: ${label} ✓`);
    }
  }

  function clearPgMessageTagFilter(clNum) {
    const num = Number(clNum || 0);
    const sel = document.getElementById(`pgMsgFilterTag-${num}`);
    if (sel) {
      sel.value = "all";
    }
    applyPgMessageFilters(num);
  }

  function onPgMessageAuthorFilterChange(clNum, _val) {
    applyPgMessageFilters(clNum);
  }

  function togglePgMessageCommentsOnly(clNum) {
    const cb = document.getElementById(`pgMsgFilterCommentsOnly-${clNum}`);
    const activeCats = getPgMessageCategoryFilters(clNum);
    if (cb && cb.checked) {
      activeCats.add("comments");
    } else {
      activeCats.delete("comments");
    }
    syncPgMessageFilterChipsDom(clNum);
    applyPgMessageFilters(clNum);
  }

  function clearAllPgMessageFilters(clNum) {
    const activeCats = getPgMessageCategoryFilters(clNum);
    activeCats.clear();
    syncPgMessageFilterChipsDom(clNum);
    const searchInput = document.getElementById(`pgMsgFilterSearch-${clNum}`);
    if (searchInput) searchInput.value = "";
    const selectAuthor = document.getElementById(`pgMsgFilterAuthor-${clNum}`);
    if (selectAuthor) selectAuthor.value = "all";
    const selectTag = document.getElementById(`pgMsgFilterTag-${clNum}`);
    if (selectTag) selectTag.value = "all";
    const cbComments = document.getElementById(`pgMsgFilterCommentsOnly-${clNum}`);
    if (cbComments) cbComments.checked = false;
    const selectPs = document.getElementById(`pgMsgFilterPs-${clNum}`);
    if (selectPs) selectPs.value = "all";
    const cbAuto = document.getElementById(`pgMsgFilterAuto-${clNum}`);
    if (cbAuto) {
      cbAuto.checked = true;
      try {
        localStorage.setItem("pg_show_automated_messages", "1");
      } catch (_) {}
    }
    applyPgMessageFilters(clNum);
    if (typeof showToast === "function") {
      showToast("Message filters reset ✓");
    }
  }

  function copyPgMessageLink(event, clNum, idx, msgId) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const anchor = msgId ? `message-${msgId}` : `message-${idx}`;
    let permalink = `#${anchor}`;
    if (typeof window !== "undefined" && window.location) {
      permalink = `${window.location.origin}${window.location.pathname}?cl=${clNum}#${anchor}`;
      if (window.history && typeof window.history.replaceState === "function") {
        window.history.replaceState(null, "", `#${anchor}`);
      }
    }
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(permalink);
    }
    if (typeof showToast === "function") {
      showToast(`Copied link to message #${idx + 1} to clipboard ✓`);
    }
    const card = document.getElementById(`pgMsg-${clNum}-${idx}`);
    if (card && card.classList) {
      card.classList.add("flash-highlight");
      setTimeout(() => {
        if (card.classList) card.classList.remove("flash-highlight");
      }, 1200);
    }
  }

  function checkPgMessageHashAnchor() {
    if (typeof window === "undefined" || !window.location) return;
    const hash = window.location.hash || "";
    if (!hash.startsWith("#message-")) return;
    const target = hash.replace("#message-", "");
    const card = document.querySelector(`.pg-message-card[data-msg-id="${target}"], .pg-message-card[data-msg-idx="${target}"]`);
    if (card && card.classList) {
      card.classList.remove("pg-msg-collapsed");
      card.classList.add("pg-msg-expanded");
      const ch = card.querySelector(".pg-msg-chevron");
      if (ch) ch.textContent = "▼";
      if (typeof card.scrollIntoView === "function") {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      card.classList.add("flash-highlight");
      setTimeout(() => {
        if (card.classList) card.classList.remove("flash-highlight");
      }, 1400);
    }
  }

  function formatPgQuoteText(rawText, authorName = "", dateStr = "") {
    const clean = String(rawText || "").trim();
    if (!clean) return "";
    const lines = clean.split("\n").map(l => `> ${l}`).join("\n");
    let header = "";
    if (authorName && dateStr) {
      header = `> On ${dateStr}, ${authorName} wrote:\n`;
    } else if (authorName) {
      header = `> ${authorName} wrote:\n`;
    }
    return `${header}${lines}\n\n`;
  }

  function getPgMessageByIndex(clNum, idx) {
    const num = Number(clNum || 0);
    const i = Number(idx);
    if (Number.isNaN(i) || i < 0) return null;
    const c = (typeof findChangeByNumber === "function") ? findChangeByNumber(num) : null;
    let msgs = (c && Array.isArray(c.messages)) ? c.messages : [];
    if (msgs.length === 0 && state.diffBundlesByCl && state.diffBundlesByCl[num] && Array.isArray(state.diffBundlesByCl[num].messages)) {
      msgs = state.diffBundlesByCl[num].messages;
    }
    return msgs[i] || null;
  }

  function quotePgMessage(event, clNum, idx) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    state.selectedClNumber = num;

    const m = getPgMessageByIndex(num, idx);
    const card = document.getElementById(`pgMsg-${num}-${idx}`);

    let selectedText = "";
    if (typeof window !== "undefined" && window.getSelection) {
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && sel.toString().trim()) {
        const range = sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
        if (range && card && card.contains(range.commonAncestorContainer)) {
          selectedText = sel.toString().trim();
        }
      }
    }

    const textToQuote = selectedText || (m && m.message) || (card && card.querySelector(".pg-message-body") ? card.querySelector(".pg-message-body").innerText.trim() : "");
    if (!textToQuote) {
      if (typeof showToast === "function") showToast("No message content to quote");
      return;
    }

    const author = (m && m.author_name) || (card ? card.getAttribute("data-author") : "") || "Reviewer";
    const dateStr = (m && m.date) || "";
    const quoteBlock = formatPgQuoteText(textToQuote, author, dateStr);

    ensureStagedVoteContextSynced();
    const curComment = String(state.stagedVote.comment || "");
    if (!curComment) {
      state.stagedVote.comment = quoteBlock;
    } else {
      const separator = curComment.endsWith("\n\n") ? "" : (curComment.endsWith("\n") ? "\n" : "\n\n");
      state.stagedVote.comment = curComment + separator + quoteBlock;
    }

    openReplyDialog({ focusComment: true });

    const noteInp = document.getElementById("stagedVoteCommentInput");
    if (noteInp) {
      noteInp.value = state.stagedVote.comment;
      if (typeof noteInp.focus === "function") noteInp.focus();
      noteInp.selectionStart = noteInp.selectionEnd = noteInp.value.length;
      if (typeof noteInp.dispatchEvent === "function") {
        noteInp.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }

    if (card && card.classList) {
      card.classList.add("flash-highlight");
      setTimeout(() => {
        if (card.classList) card.classList.remove("flash-highlight");
      }, 1200);
    }

    if (typeof showToast === "function") {
      showToast(`Quoted message from ${author} in Reply dialog (q) ✓`);
    }
  }

  function copyPgMessageText(event, clNum, idx) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const m = getPgMessageByIndex(num, idx);
    const card = document.getElementById(`pgMsg-${num}-${idx}`);
    const text = (m && m.message) || (card && card.querySelector(".pg-message-body") ? card.querySelector(".pg-message-body").innerText.trim() : "");
    if (!text) {
      if (typeof showToast === "function") showToast("No text to copy");
      return;
    }

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(text);
    }

    const btn = event && (event.currentTarget || event.target);
    if (btn) {
      const origHtml = btn.innerHTML;
      btn.innerHTML = "✓ Copied!";
      btn.style.color = "var(--g-accent, #8ab4f8)";
      setTimeout(() => {
        btn.innerHTML = origHtml;
        btn.style.color = "";
      }, 1200);
    }

    if (typeof showToast === "function") {
      showToast("Copied message text to clipboard ✓");
    }
  }

  function quoteInlineCommentInReply(itemId, clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const idStr = String(itemId || "");
    const item = (typeof findInlineItemById === "function") ? findInlineItemById(num, idStr) : null;
    if (!item) return;

    if (typeof openInlineReplyComposer === "function") {
      openInlineReplyComposer(num, idStr);
    }

    const cardEl = document.getElementById(`inlineItem-${idStr}`);
    let selectedText = "";
    if (typeof window !== "undefined" && window.getSelection) {
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && sel.toString().trim()) {
        const range = sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
        if (range && cardEl && cardEl.contains(range.commonAncestorContainer)) {
          selectedText = sel.toString().trim();
        }
      }
    }

    let textToQuote = selectedText;
    let author = item.root_author_name || item.author_name || "reviewer";
    let dateStr = "";
    if (!textToQuote) {
      if (Array.isArray(item.messages) && item.messages.length > 0) {
        const lastMsg = item.messages[item.messages.length - 1];
        textToQuote = lastMsg.message || "";
        author = lastMsg.author_name || author;
        dateStr = lastMsg.date || "";
      } else {
        textToQuote = item.message || "";
      }
    }

    const quoteBlock = formatPgQuoteText(textToQuote, author, dateStr);
    if (!state.openReplyByItemId[idStr]) {
      state.openReplyByItemId[idStr] = {
        draft: "",
        unresolved: item.unresolved !== false,
        submitting: false,
        error: "",
        isSavedDraft: false,
      };
    }
    const replyState = state.openReplyByItemId[idStr];
    const curDraft = String(replyState.draft || "");
    if (!curDraft) {
      replyState.draft = quoteBlock;
    } else {
      const sep = curDraft.endsWith("\n\n") ? "" : (curDraft.endsWith("\n") ? "\n" : "\n\n");
      replyState.draft = curDraft + sep + quoteBlock;
    }

    const ta = document.getElementById(`commentReplyInput-${idStr}`);
    if (ta) {
      ta.value = replyState.draft;
      if (typeof ta.focus === "function") ta.focus();
      ta.selectionStart = ta.selectionEnd = ta.value.length;
      if (typeof autoResizePgTextarea === "function") {
        autoResizePgTextarea(ta);
      }
    }

    if (typeof showToast === "function") {
      showToast(`Quoted comment from ${author} in reply (q) ✓`);
    }
  }

  function togglePgAutomatedMessages(clNum) {
    const num = Number(clNum || 0);
    const cb = document.getElementById(`pgMsgFilterAuto-${num}`);
    if (cb) {
      try {
        localStorage.setItem("pg_show_automated_messages", cb.checked ? "1" : "0");
      } catch (_) {}
      const activeCats = getPgMessageCategoryFilters(num);
      if (!cb.checked && activeCats.has("automated")) {
        activeCats.delete("automated");
        syncPgMessageFilterChipsDom(num);
      }
    }
    applyPgMessageFilters(num);
  }

  function onPgMessagePsFilterChange(clNum, _val) {
    applyPgMessageFilters(clNum);
  }

  function applyPgMessageFilters(clNum) {
    const section = document.getElementById(`pgChangeLog-${clNum}`);
    if (!section || typeof section.querySelectorAll !== "function") return;
    const activeCats = getPgMessageCategoryFilters(clNum);
    const hasHumanFilter = activeCats.has("human");
    const hasBotFilter = activeCats.has("automated");
    const hasRobotFilter = activeCats.has("robot");
    const hasCommentsFilter = activeCats.has("comments");
    const hasVotesFilter = activeCats.has("votes");
    const hasAttnFilter = activeCats.has("attention");

    const cbAuto = document.getElementById(`pgMsgFilterAuto-${clNum}`);
    const showAuto = (cbAuto && typeof cbAuto.checked === "boolean") ? cbAuto.checked : true;
    const selectPs = document.getElementById(`pgMsgFilterPs-${clNum}`);
    const filterPs = (selectPs && selectPs.value) ? selectPs.value : "all";
    const selectAuthor = document.getElementById(`pgMsgFilterAuthor-${clNum}`);
    const filterAuthor = (selectAuthor && selectAuthor.value) ? selectAuthor.value : "all";
    const selectTag = document.getElementById(`pgMsgFilterTag-${clNum}`);
    const filterTag = (selectTag && selectTag.value) ? selectTag.value : "all";
    const cbComments = document.getElementById(`pgMsgFilterCommentsOnly-${clNum}`);
    const commentsOnly = (cbComments && typeof cbComments.checked === "boolean") ? cbComments.checked : false;
    const searchInput = document.getElementById(`pgMsgFilterSearch-${clNum}`);
    const rawQuery = searchInput ? (searchInput.value || "") : "";
    const query = rawQuery.trim().toLowerCase();

    const clearSearchBtn = document.getElementById(`btnPgMsgSearchClear-${clNum}`);
    if (clearSearchBtn && clearSearchBtn.style) {
      clearSearchBtn.style.display = rawQuery.length > 0 ? "inline-flex" : "none";
    }

    const isUnfiltered = (
      !query &&
      (!filterAuthor || filterAuthor === "all") &&
      (!filterTag || filterTag === "all") &&
      !commentsOnly &&
      (!filterPs || filterPs === "all") &&
      showAuto &&
      activeCats.size === 0
    );
    if (isUnfiltered && section._msgFiltersUnfilteredClean) {
      return;
    }

    let visibleCount = 0;
    const cards = section.querySelectorAll(".pg-message-card");
    cards.forEach(card => {
      const isBot = card.getAttribute("data-is-bot") === "1";
      const cardPs = card.getAttribute("data-ps");
      const cardAuthor = card.getAttribute("data-author") || "";
      const hasComments = card.getAttribute("data-has-comments") === "1";
      const hasRobot = card.getAttribute("data-has-robot") === "1";
      const hasVotes = card.getAttribute("data-has-votes") === "1";
      const hasAttn = card.getAttribute("data-has-attn") === "1";
      const cardTag = card.getAttribute("data-tag") || "";
      const bodyEl = typeof card.querySelector === "function" ? card.querySelector(".pg-message-body") : null;

      let visible = true;
      if (!showAuto && isBot) {
        if (!hasBotFilter && !hasRobotFilter && !(commentsOnly && hasComments)) {
          visible = false;
        }
      }
      if (filterPs !== "all" && cardPs !== filterPs) {
        visible = false;
      }
      if (filterAuthor === "human" && isBot) {
        visible = false;
      } else if (filterAuthor !== "all" && filterAuthor !== "human" && cardAuthor !== filterAuthor) {
        visible = false;
      }
      if (filterTag !== "all" && cardTag !== filterTag) {
        visible = false;
      }
      if (commentsOnly && !hasComments) {
        visible = false;
      }
      if (hasRobotFilter && !hasRobot) {
        visible = false;
      }
      if (activeCats.size > 0) {
        if (hasHumanFilter && !hasBotFilter && !hasRobotFilter && isBot) {
          visible = false;
        } else if (hasBotFilter && !hasHumanFilter && !isBot) {
          visible = false;
        } else if (hasRobotFilter && !hasRobot) {
          visible = false;
        }
        const hasContentFilter = hasCommentsFilter || hasVotesFilter || hasAttnFilter || hasRobotFilter;
        if (hasContentFilter) {
          const matchesContent =
            (hasCommentsFilter && hasComments) ||
            (hasVotesFilter && hasVotes) ||
            (hasAttnFilter && hasAttn) ||
            (hasRobotFilter && hasRobot);
          if (!matchesContent) {
            visible = false;
          }
        }
      }
      let snippetEl = null;
      if (visible && query) {
        snippetEl = typeof card.querySelector === "function" ? card.querySelector(".pg-msg-summary-snippet") : null;
        const cardText = (bodyEl ? (bodyEl.textContent || "") : "");
        const cardSummary = (snippetEl ? (snippetEl.textContent || "") : "");
        const matchesAuthor = cardAuthor.toLowerCase().includes(query);
        const matchesText = cardText.toLowerCase().includes(query);
        const matchesSummary = cardSummary.toLowerCase().includes(query);
        const matchesTag = cardTag.toLowerCase().includes(query);
        const matchesPs = `ps ${cardPs}`.toLowerCase().includes(query) || `patchset ${cardPs}`.toLowerCase().includes(query) || (query.startsWith("ps:") && query.slice(3).trim() === cardPs);
        if (!matchesAuthor && !matchesText && !matchesSummary && !matchesTag && !matchesPs) {
          visible = false;
        }
      }

      if (bodyEl && typeof bodyEl.innerHTML === "string") {
        if (visible && query) {
          if (!bodyEl.dataset) bodyEl.dataset = {};
          if (!bodyEl.dataset.originalHtml) {
            bodyEl.dataset.originalHtml = bodyEl.innerHTML;
          }
          bodyEl.innerHTML = highlightPgSearchText(bodyEl.dataset.originalHtml, rawQuery.trim());
        } else if (bodyEl.dataset && bodyEl.dataset.originalHtml) {
          bodyEl.innerHTML = bodyEl.dataset.originalHtml;
        }
      }
      if (!snippetEl && (query || (card._hasSearchSnippetHighlight))) {
        snippetEl = typeof card.querySelector === "function" ? card.querySelector(".pg-msg-summary-snippet") : null;
      }
      if (snippetEl && typeof snippetEl.textContent === "string") {
        if (visible && query) {
          if (!snippetEl.dataset) snippetEl.dataset = {};
          if (!snippetEl.dataset.originalText) {
            snippetEl.dataset.originalText = snippetEl.textContent;
          }
          card._hasSearchSnippetHighlight = true;
          snippetEl.innerHTML = highlightPgSearchText(escapeHtml(snippetEl.dataset.originalText), rawQuery.trim());
        } else if (snippetEl.dataset && snippetEl.dataset.originalText) {
          snippetEl.textContent = snippetEl.dataset.originalText;
          card._hasSearchSnippetHighlight = false;
        }
      }

      if (visible) {
        if (card.classList) {
          card.classList.remove("is-hidden");
        }
        visibleCount++;
      } else {
        if (card.classList) {
          card.classList.add("is-hidden");
        }
      }
    });

    const isFiltered = Boolean(
      query ||
      (filterAuthor && filterAuthor !== "all") ||
      (filterTag && filterTag !== "all") ||
      commentsOnly ||
      (filterPs && filterPs !== "all") ||
      !showAuto ||
      activeCats.size > 0
    );

    const badge = typeof section.querySelector === "function"
      ? section.querySelector(".pg-change-log-count-badge")
      : null;
    if (badge) {
      if (isFiltered) {
        badge.textContent = `Showing ${visibleCount} of ${cards.length} messages`;
      } else {
        badge.textContent = `${cards.length} messages`;
      }
    }

    const clearFiltersBtn = document.getElementById(`btnPgClearMsgFilters-${clNum}`);
    if (clearFiltersBtn && clearFiltersBtn.style) {
      clearFiltersBtn.style.display = isFiltered ? "inline-flex" : "none";
    }
    section._msgFiltersUnfilteredClean = !isFiltered;
  }

  function scrollToChangeLog(clNum) {
    const el = document.getElementById(`pgChangeLog-${clNum}`);
    if (el) {
      if (typeof el.scrollIntoView === "function") {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      if (el.classList) {
        el.classList.add("flash-highlight");
        setTimeout(() => {
          if (el.classList) el.classList.remove("flash-highlight");
        }, 1200);
      }
      const searchInput = document.getElementById(`pgMsgFilterSearch-${clNum}`);
      if (searchInput && typeof searchInput.focus === "function") {
        setTimeout(() => {
          try { searchInput.focus(); } catch (_) {}
        }, 350);
      }
    }
  }

  function getAllPgCommentsForCl(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const c = resolveBundleChangeObj(bundle, num);
    const serverThreads = (bundle && (bundle.comment_threads || bundle.unresolved_threads)) ||
                          (c && (c.comment_threads || c.unresolved_threads)) || _PG_EMPTY_ARRAY;
    const localItems = (state.inlineCommentsByCl && state.inlineCommentsByCl[num]) || _PG_EMPTY_ARRAY;
    if (serverThreads.length === 0 && localItems.length === 0) {
      return _PG_EMPTY_ARRAY;
    }
    if (c && serverThreads.length === 1 && localItems.length === 0) {
      const t0 = serverThreads[0];
      const acSub = c._allCommentsSub;
      const msgs0 = (t0 && Array.isArray(t0.messages)) ? t0.messages : _PG_EMPTY_ARRAY;
      const eff0 = t0 ? (t0.latest_message || (msgs0.length > 0 && msgs0[msgs0.length - 1] && msgs0[msgs0.length - 1].message) || (msgs0[0] && msgs0[0].message) || t0.message || "") : "";
      if (
        acSub &&
        acSub.srvRef === serverThreads &&
        acSub.t0Ref === t0 &&
        acSub.id === (t0.id || t0.root_id || t0.leaf_id || "srv-0") &&
        acSub.unres === (t0.unresolved !== false) &&
        acSub.effMsg === eff0 &&
        acSub.msgsLen === msgs0.length
      ) {
        return acSub.result;
      }
    }
    const result = [];
    const seenIds = new Set();

    serverThreads.forEach((t, idx) => {
      const id = String(t.id || t.root_id || t.leaf_id || `srv-${idx}`);
      const uiItemId = t._ui_item_id || `srv-thread-${num}-${idx}`;
      t._ui_item_id = uiItemId;
      if (seenIds.has(id)) return;
      seenIds.add(id);
      if (uiItemId) seenIds.add(uiItemId);
      const msgs = Array.isArray(t.messages) ? t.messages : [];
      const latestMsgObj = msgs.length > 0 ? msgs[msgs.length - 1] : null;
      const effectiveMsg = String(t.latest_message || (latestMsgObj && latestMsgObj.message) || (msgs[0] && msgs[0].message) || t.message || "").trim();
      result.push({
        id: id,
        ui_item_id: uiItemId,
        root_id: String(t.root_id || id),
        leaf_id: String(t.leaf_id || ""),
        comment_id: String(t.leaf_id || t.root_id || id),
        server_id: String(t.id || ""),
        file_path: t.file_path || "",
        line: Number(t.line || t.start_line || 1),
        start_line: Number(t.start_line || t.line || 1),
        end_line: Number(t.end_line || t.line || 1),
        patch_set: Number(t.patch_set || (bundle && bundle.patch_set) || 1),
        author_name: t.author_name || (latestMsgObj && latestMsgObj.author_name) || "Reviewer",
        author_email: t.author_email || (latestMsgObj && latestMsgObj.author_email) || "",
        updated: t.updated || "",
        unresolved: t.unresolved !== false,
        is_robot: Boolean(t.is_robot || t.robot_id),
        robot_id: t.robot_id || "",
        latest_message: effectiveMsg,
        message: effectiveMsg,
        messages: msgs,
        is_draft: false,
        kind: "comment",
        change_message_id: t.change_message_id || "",
      });
    });

    localItems.forEach(item => {
      if (!item || item.alt_id) return;
      const isDraft = item.kind === "draft" || item.is_draft || Boolean(item.draft_mode);
      const isAgent = item.kind === "agent_question";
      const id = String(item.id || `local-${state.nextInlineItemId++}`);
      if (seenIds.has(id)) return;
      seenIds.add(id);
      const effMsg = String(item.message || (item.messages && item.messages[item.messages.length - 1] && item.messages[item.messages.length - 1].message) || "").trim();
      result.push({
        id: id,
        ui_item_id: id,
        root_id: id,
        leaf_id: id,
        comment_id: id,
        file_path: item.file_path || item.file || "",
        line: Number(item.line || item.start_line || 1),
        start_line: Number(item.start_line || item.line || 1),
        end_line: Number(item.end_line || item.line || 1),
        patch_set: Number(item.patch_set || (bundle && bundle.patch_set) || 1),
        author_name: item.author_name || (isAgent ? "Jetski Agent" : "You (Draft)"),
        author_email: item.author_email || "",
        updated: item.updated || "Just now",
        unresolved: item.unresolved !== false,
        is_robot: false,
        robot_id: "",
        latest_message: effMsg,
        message: effMsg,
        messages: item.messages || [{ message: item.message || "", author_name: item.author_name || "You" }],
        is_draft: isDraft,
        kind: isAgent ? "agent_question" : (isDraft ? "draft" : "comment"),
        change_message_id: "",
      });
    });

    result.sort((a, b) => {
      const cmp = String(a.file_path).localeCompare(String(b.file_path));
      if (cmp !== 0) return cmp;
      return a.line - b.line;
    });

    if (c && serverThreads.length === 1 && localItems.length === 0) {
      const t0 = serverThreads[0];
      const msgs0 = (t0 && Array.isArray(t0.messages)) ? t0.messages : _PG_EMPTY_ARRAY;
      const eff0 = t0 ? (t0.latest_message || (msgs0.length > 0 && msgs0[msgs0.length - 1] && msgs0[msgs0.length - 1].message) || (msgs0[0] && msgs0[0].message) || t0.message || "") : "";
      c._allCommentsSub = {
        srvRef: serverThreads, t0Ref: t0,
        id: t0 ? (t0.id || t0.root_id || t0.leaf_id || "srv-0") : "",
        unres: t0 ? (t0.unresolved !== false) : false,
        effMsg: eff0, msgsLen: msgs0.length, result
      };
    }

    return result;
  }

  function parsePgCommentsFromMessageText(text, ps = 1) {
    if (!text) return [];
    const lines = String(text).split("\n");
    const results = [];
    let curFile = "";
    let curLine = null;
    let curMsg = [];

    const flush = () => {
      const bodyText = curMsg.join("\n").trim();
      if (curFile && bodyText) {
        const lineNum = curLine !== null ? curLine : 1;
        results.push({
          id: `parsed-${curFile.replace(/[^a-zA-Z0-9_-]/g, "_")}-${lineNum}-${results.length}`,
          file_path: curFile,
          line: lineNum,
          start_line: lineNum,
          end_line: lineNum,
          patch_set: ps,
          author_name: "Reviewer",
          latest_message: bodyText,
          unresolved: true,
          messages: [{ message: bodyText, author_name: "Reviewer" }],
          is_parsed: true,
          change_message_id: "",
        });
      }
      curMsg = [];
      curLine = null;
    };

    for (const rawLine of lines) {
      const trimmed = rawLine.trim();
      const fileMatch = trimmed.match(/^File\s+([^\s:]+):?$/i);
      if (fileMatch) {
        flush();
        curFile = fileMatch[1].replace(/[:`]/g, "").trim();
        continue;
      }
      const lineMatch = trimmed.match(/^Line\s+(\d+):?\s*(.*)$/i);
      if (lineMatch) {
        flush();
        curLine = Number(lineMatch[1]);
        if (lineMatch[2]) curMsg.push(lineMatch[2]);
        continue;
      }
      if (curFile && trimmed) {
        curMsg.push(trimmed);
      }
    }
    flush();
    return results;
  }

  function getPgCommentsForMessage(clNum, m, bundle) {
    if (!m) return [];
    if (Array.isArray(m.comments) && m.comments.length > 0) {
      return m.comments;
    }
    const all = getAllPgCommentsForCl(clNum);
    const mId = String(m.id || "");
    const mPs = Number(m.patch_set || 0);
    const mAuthor = String(m.author_name || "").trim().toLowerCase();
    const mEmail = String(m.author_email || "").trim().toLowerCase();
    const mText = String(m.message || "");

    // 1. Direct match by change_message_id if present
    if (mId) {
      const directMatches = all.filter(t => {
        if (t.change_message_id && String(t.change_message_id) === mId) return true;
        if (Array.isArray(t.messages)) {
          return t.messages.some(msg => msg && String(msg.change_message_id) === mId);
        }
        return false;
      });
      if (directMatches.length > 0) {
        return directMatches.map(t => {
          const matchedMsg = Array.isArray(t.messages) ? t.messages.find(msg => msg && String(msg.change_message_id) === mId) : null;
          if (matchedMsg && matchedMsg.message) {
            return Object.assign({}, t, {
              latest_message: matchedMsg.message,
              message: matchedMsg.message,
              comment_id: matchedMsg.comment_id || t.comment_id,
            });
          }
          return t;
        });
      }
    }

    // 2. Match by patch set and author ONLY when message explicitly mentions comments
    const hasCommentsMention = /\(\d+\s+comments?\)/i.test(mText) || /\b(?:File|Line)\s+/i.test(mText);
    if (mPs > 0 && hasCommentsMention) {
      const authorMatches = all.filter(t => {
        if (t.is_draft) return false;
        const msgMatch = (Array.isArray(t.messages) && t.messages.length > 0)
          ? t.messages.some(msg => {
              const p = Number(msg.patch_set || t.patch_set || 0);
              if (p !== mPs) return false;
              const aName = String(msg.author_name || t.author_name || "").trim().toLowerCase();
              const aEmail = String(msg.author_email || t.author_email || "").trim().toLowerCase();
              return (mAuthor && aName === mAuthor) || (mEmail && aEmail === mEmail);
            })
          : false;
        if (msgMatch) return true;
        if (t.patch_set !== mPs) return false;
        const tAuthor = String(t.author_name || "").trim().toLowerCase();
        const tEmail = String(t.author_email || "").trim().toLowerCase();
        return (mAuthor && tAuthor === mAuthor) || (mEmail && tEmail === mEmail);
      });
      if (authorMatches.length > 0) return authorMatches;
    }

    // 3. Fallback: parse comments from message text if formatted with File / Line
    if (/\b(?:File|Line)\s+/i.test(mText) && (/\(\d+\s+comments?\)/i.test(mText) || /File\s+[^\s:]+:\s*Line/i.test(mText))) {
      const parsed = parsePgCommentsFromMessageText(mText, mPs);
      if (parsed.length > 0) return parsed;
    }

    return [];
  }

  function renderPgMessageCommentsListHtml(clNum, comments) {
    if (!Array.isArray(comments) || comments.length === 0) return "";
    const num = Number(clNum || 0);
    return `<div class="pg-msg-comments-list">
      <div class="pg-msg-comments-header">💬 Comments on this review (${comments.length}):</div>
      ${comments.map(t => {
        const fpath = escapeHtml(t.file_path || "");
        const line = Number(t.line || t.start_line || 1);
        const commentId = String(t.ui_item_id || t.id || t.root_id || t.comment_id || "");
        const unres = t.unresolved !== false;
        const statusCls = unres ? "has-unresolved" : "resolved";
        const statusLabel = unres ? "⚠️ Unresolved" : "✓ Resolved";
        const isRobot = Boolean(t.is_robot || t.robot_id);
        const robotBadgeHtml = isRobot
          ? `<span class="pg-robot-badge" style="margin-left:4px;" title="Automated check / Robot comment">🤖 Robot</span>`
          : "";
        const robotRunHtml = (isRobot && t.robot_id)
          ? `<span class="pg-robot-run-pill" style="margin-left:4px;" title="Robot ID: ${escapeHtml(t.robot_id)}">${escapeHtml(t.robot_id)}</span>`
          : "";
        const msgText = String(t.latest_message || (t.messages && t.messages[0] && t.messages[0].message) || t.message || "").trim();
        const safeCommentId = escapeJsQuote(commentId);
        const safeFilePath = escapeJsQuote(t.file_path || "");
        return `<div class="pg-msg-comment-item ${isRobot ? "is-robot" : ""}" id="pgMsgComment-${num}-${escapeHtml(commentId)}" data-comment-id="${escapeHtml(commentId)}" data-file="${fpath}" data-line="${line}">
          <div class="pg-msg-comment-loc">
            <span class="pg-msg-comment-file" onclick="scrollToPgCommentOrFile(${num}, '${safeFilePath}', ${line}, '${safeCommentId}')" title="Jump to ${escapeHtml(t.file_path || "file")}:${line} in diff viewer">
              <code>${fpath}:${line}</code>
            </span>
            <span class="diff-inline-status-pill ${statusCls}">${statusLabel}</span>
            ${robotBadgeHtml}
            ${robotRunHtml}
            <button type="button" class="pg-btn-subtle pg-btn-jump-diff" onclick="event.stopPropagation(); scrollToPgCommentOrFile(${num}, '${safeFilePath}', ${line}, '${safeCommentId}')" title="Jump to Line ${line} in diff viewer">
              Jump to diff ↗
            </button>
          </div>
          <div class="pg-msg-comment-snippet">${renderInlineMarkdownHtml(msgText, false)}</div>
        </div>`;
      }).join("")}
    </div>`;
  }

  function renderPgCommentsViewHtml(c, bundle) {
    const num = Number(c && c.number ? c.number : state.selectedClNumber || 0);
    const comments = getAllPgCommentsForCl(num);
    const activeFilter = (state.commentsViewFilterByCl && state.commentsViewFilterByCl[num]) || "all";
    const activeAuthor = (typeof getPgCommentsAuthorFilter === "function") ? getPgCommentsAuthorFilter(num) : "all";
    const searchQuery = String((state.commentsViewQueryByCl && state.commentsViewQueryByCl[num]) || "").trim().toLowerCase();
    const isCollapsedAll = Boolean(state.commentsViewCollapsedByCl && state.commentsViewCollapsedByCl[num]);
    if (c && comments.length <= 1) {
      const cvSub = c._cvSub;
      const t0 = comments[0];
      const msgsLen0 = (t0 && Array.isArray(t0.messages)) ? t0.messages.length : 0;
      if (
        cvSub &&
        cvSub.len === comments.length &&
        cvSub.activeFilter === activeFilter &&
        cvSub.activeAuthor === activeAuthor &&
        cvSub.searchQuery === searchQuery &&
        cvSub.isCollapsedAll === isCollapsedAll &&
        (!t0 || (
          cvSub.t0Id === (t0.id || t0.ui_item_id || "") &&
          cvSub.t0Fp === (t0.file_path || "") &&
          cvSub.t0Line === (t0.line || 0) &&
          cvSub.t0Unres === Boolean(t0.unresolved) &&
          cvSub.t0Draft === Boolean(t0.is_draft) &&
          cvSub.t0Robot === Boolean(t0.is_robot) &&
          cvSub.t0Author === (t0.author_name || "") &&
          cvSub.t0Msg === (t0.latest_message || t0.message || "") &&
          cvSub.t0MsgsLen === msgsLen0
        )) &&
        c._cachedEmptyCommentsViewKey === cvSub.key &&
        typeof c._cachedEmptyCommentsViewHtml === "string"
      ) {
        return c._cachedEmptyCommentsViewHtml;
      }
    }
    let commentsViewKey = "";
    if (c && comments.length <= 64) {
      let cSig = String(comments.length);
      for (let i = 0; i < comments.length; i++) {
        const t = comments[i];
        if (!t) continue;
        cSig += `:${t.id || t.ui_item_id || ""}:${t.file_path || ""}:${t.line || 0}:${t.unresolved ? 1 : 0}:${t.is_draft ? 1 : 0}:${t.is_robot ? 1 : 0}:${t.author_name || ""}:${t.latest_message || t.message || ""}:${Array.isArray(t.messages) ? t.messages.length : 0}`;
      }
      commentsViewKey = `${num}|${cSig}|${activeFilter}|${activeAuthor}|${searchQuery}|${isCollapsedAll ? 1 : 0}`;
      if (c._cachedEmptyCommentsViewKey === commentsViewKey && typeof c._cachedEmptyCommentsViewHtml === "string") {
        return c._cachedEmptyCommentsViewHtml;
      }
    }
    const allAuthors = (typeof getPgCommentAuthors === "function") ? getPgCommentAuthors(num) : [];

    let unresCount = 0;
    let resCount = 0;
    let draftsCount = 0;
    let robotCount = 0;

    comments.forEach(t => {
      if (t.is_draft) draftsCount++;
      if (t.is_robot) robotCount++;
      if (t.unresolved) unresCount++;
      else resCount++;
    });

    let filtered = comments.filter(t => {
      if (activeFilter === "unresolved" && !t.unresolved) return false;
      if (activeFilter === "resolved" && t.unresolved) return false;
      if (activeFilter === "drafts" && !t.is_draft) return false;
      if (activeFilter === "robot" && !t.is_robot) return false;
      if (activeAuthor && activeAuthor !== "all") {
        const aName = String(t.author_name || (t.is_robot ? (t.robot_id || "Robot") : "Reviewer")).trim();
        if (aName !== activeAuthor) return false;
      }
      if (searchQuery) {
        const path = String(t.file_path || "").toLowerCase();
        const author = String(t.author_name || "").toLowerCase();
        const text = String(t.latest_message || "").toLowerCase();
        const lineStr = `line ${t.line}`;
        return path.includes(searchQuery) || author.includes(searchQuery) || text.includes(searchQuery) || lineStr.includes(searchQuery);
      }
      return true;
    });

    const byFile = new Map();
    filtered.forEach(t => {
      const f = t.file_path || "/COMMIT_MSG";
      if (!byFile.has(f)) byFile.set(f, []);
      byFile.get(f).push(t);
    });

    let groupsHtml = "";
    if (byFile.size === 0) {
      groupsHtml = `<div class="pg-no-comments" style="padding:48px 16px;text-align:center;color:var(--g-muted);">
        <div style="font-size:32px;margin-bottom:8px;">💬</div>
        <div style="font-size:14px;font-weight:600;color:var(--g-text);margin-bottom:4px;">No comments found</div>
        <div style="font-size:12px;">${comments.length === 0 ? "No inline or file comments recorded on this change." : "No comments match the active filters."}</div>
      </div>`;
    } else {
      groupsHtml = Array.from(byFile.entries()).map(([filePath, fileThreads]) => {
        const safeFile = escapeHtml(filePath);
        const threadsListHtml = fileThreads.map(t => {
          const line = Number(t.line || 1);
          const safeId = escapeHtml(t.ui_item_id || t.id || t.root_id || t.comment_id || "");
          const statusCls = t.is_draft ? "draft-pill" : (t.unresolved ? "has-unresolved" : "resolved");
          const statusLabel = t.is_draft ? "📝 Draft" : (t.unresolved ? "⚠️ Unresolved" : "✓ Resolved");
          const robotBadge = t.is_robot ? `<span class="pg-robot-badge">🤖 Robot</span>` : "";
          const psPill = t.patch_set ? `<span class="pg-msg-ps-pill">PS ${t.patch_set}</span>` : "";
          const authorInitials = getPgAuthorInitials(t.author_name);
          const avatarHtml = `<span class="pg-avatar-chip" title="${escapeHtml(t.author_name)}">${escapeHtml(authorInitials)}</span>`;
          const msgBodyHtml = (Array.isArray(t.messages) && t.messages.length > 1)
            ? renderInlineCommentMessagesHtml(t)
            : `<div class="diff-inline-comment-body">${renderInlineMarkdownHtml(t.latest_message || t.message || (t.messages && t.messages[0] && t.messages[0].message) || "", false)}</div>`;

          return `<div class="pg-comments-thread-card ${t.is_draft ? "is-draft-card" : ""}" id="pgCommentsCard-${safeId}" data-thread-id="${safeId}">
            <div class="pg-comments-thread-meta">
              <div class="pg-comments-meta-left">
                <button type="button" class="pg-btn-subtle pg-line-badge-btn" onclick="scrollToPgCommentOrFile(${num}, '${escapeJsQuote(filePath)}', ${line}, '${escapeJsQuote(safeId)}')" title="Jump to Line ${line} in diff viewer">
                  Line ${line} ↗
                </button>
                <span class="pg-avatar-chip is-clickable" onclick="event.stopPropagation(); setPgCommentsAuthorFilter(${num}, '${escapeJsQuote(t.author_name)}')" title="Filter comments by ${escapeHtml(t.author_name)}">${escapeHtml(authorInitials)}</span>
                <span class="pg-thread-author is-clickable ${activeAuthor === t.author_name ? "is-active-author" : ""}" onclick="event.stopPropagation(); setPgCommentsAuthorFilter(${num}, '${escapeJsQuote(t.author_name)}')" title="Filter comments by ${escapeHtml(t.author_name)}">${escapeHtml(t.author_name)}</span>
                ${robotBadge}
                ${psPill}
                <span class="diff-inline-status-pill ${statusCls}">${statusLabel}</span>
              </div>
              <div class="pg-comments-meta-right">
                <button type="button" class="pg-btn-subtle" onclick="scrollToPgCommentOrFile(${num}, '${escapeJsQuote(filePath)}', ${line}, '${escapeJsQuote(safeId)}')" title="Jump to this comment in the diff viewer">
                  Jump to diff ↗
                </button>
              </div>
            </div>
            <div class="pg-comments-thread-content" style="${isCollapsedAll ? "display:none;" : ""}">
              ${msgBodyHtml}
            </div>
          </div>`;
        }).join("");

        return `<div class="pg-comments-file-group" data-file="${safeFile}">
          <div class="pg-comments-file-header">
            <div class="pg-comments-file-title" onclick="scrollToPgFileHeader(${num}, '${escapeJsQuote(safeFile)}')" title="Click to jump to file diff in Files tab">
              <span class="pg-comments-file-icon">📄</span>
              <span class="pg-comments-file-path"><code>${safeFile}</code></span>
              <span class="pg-comments-file-count">${fileThreads.length} comment${fileThreads.length === 1 ? "" : "s"}</span>
            </div>
            <div class="pg-comments-file-actions">
              <button type="button" class="pg-btn-subtle" onclick="scrollToPgFileHeader(${num}, '${escapeJsQuote(safeFile)}')" title="View file diff in Files tab">
                View diff ↗
              </button>
            </div>
          </div>
          <div class="pg-comments-file-body">
            ${threadsListHtml}
          </div>
        </div>`;
      }).join("");
    }

    const outHtml = `<div id="pgCommentsView-${num}" class="pg-comments-view">
      <div class="pg-comments-header">
        <div class="pg-comments-header-left">
          <div class="pg-msg-filter-chips" role="group" aria-label="Comments filter chips">
            <button type="button" class="pg-msg-chip ${activeFilter === "all" ? "is-active" : ""}" onclick="setPgCommentsViewFilter(${num}, 'all')">
              All <span class="pg-chip-count">${comments.length}</span>
            </button>
            <button type="button" class="pg-msg-chip ${activeFilter === "unresolved" ? "is-active" : ""}" onclick="setPgCommentsViewFilter(${num}, 'unresolved')">
              ⚠️ Unresolved <span class="pg-chip-count">${unresCount}</span>
            </button>
            <button type="button" class="pg-msg-chip ${activeFilter === "resolved" ? "is-active" : ""}" onclick="setPgCommentsViewFilter(${num}, 'resolved')">
              ✓ Resolved <span class="pg-chip-count">${resCount}</span>
            </button>
            ${draftsCount > 0 ? `
              <button type="button" class="pg-msg-chip ${activeFilter === "drafts" ? "is-active" : ""}" onclick="setPgCommentsViewFilter(${num}, 'drafts')">
                📝 Drafts <span class="pg-chip-count">${draftsCount}</span>
              </button>
            ` : ""}
            ${robotCount > 0 ? `
              <button type="button" class="pg-msg-chip ${activeFilter === "robot" ? "is-active" : ""}" onclick="setPgCommentsViewFilter(${num}, 'robot')">
                🤖 Robot <span class="pg-chip-count">${robotCount}</span>
              </button>
            ` : ""}
          </div>
          ${allAuthors.length > 1 ? `
            <div class="pg-comments-author-select-wrap">
              <label for="pgCommentsAuthorSelect-${num}" class="pg-comments-author-label">Author:</label>
              <select id="pgCommentsAuthorSelect-${num}" class="pg-comments-author-select" onchange="setPgCommentsAuthorFilter(${num}, this.value)" title="Filter comments by author / reviewer">
                <option value="all" ${activeAuthor === "all" ? "selected" : ""}>All authors (${comments.length})</option>
                ${allAuthors.map(a => `<option value="${escapeHtml(a.name)}" ${activeAuthor === a.name ? "selected" : ""}>${escapeHtml(a.name)} (${a.total}${a.unresolvedCount > 0 ? ` · ${a.unresolvedCount} unres` : ""})</option>`).join("")}
              </select>
              ${activeAuthor !== "all" ? `
                <button type="button" class="pg-author-filter-active-chip" onclick="clearPgCommentsAuthorFilter(${num})" title="Clear author filter">
                  <span>👤 ${escapeHtml(activeAuthor)}</span> <span class="pg-chip-clear">✕</span>
                </button>
              ` : ""}
            </div>
          ` : ""}
        </div>
        <div class="pg-comments-header-right">
          <div class="pg-msg-search-wrap">
            <span class="pg-msg-search-icon">🔍</span>
            <input type="text" id="pgCommentsSearchInput-${num}" class="pg-msg-search-input" placeholder="Filter comments..." value="${escapeHtml(searchQuery)}" oninput="onPgCommentsSearchInput(${num}, this.value)">
            ${searchQuery ? `<button type="button" class="pg-msg-search-clear" onclick="clearPgCommentsSearch(${num})">✕</button>` : ""}
          </div>
          <button type="button" class="pg-btn-subtle" onclick="togglePgCommentsViewCollapse(${num})" title="Expand or collapse all comment thread bodies">
            ${isCollapsedAll ? "Expand all" : "Collapse all"}
          </button>
          <button type="button" class="pg-btn-subtle" onclick="selectPgChangeTab(${num}, 'files')" title="Back to Files &amp; Diffs (Shortcut: Shift+K)">
            ← Back to Files
          </button>
        </div>
      </div>
      <div class="pg-comments-list-body">
        ${groupsHtml}
      </div>
    </div>`;
    if (c && commentsViewKey) {
      c._cachedEmptyCommentsViewKey = commentsViewKey;
      c._cachedEmptyCommentsViewHtml = outHtml;
      if (comments.length <= 1) {
        const t0 = comments[0];
        c._cvSub = {
          len: comments.length, activeFilter, activeAuthor, searchQuery, isCollapsedAll,
          t0Id: t0 ? (t0.id || t0.ui_item_id || "") : "", t0Fp: t0 ? (t0.file_path || "") : "",
          t0Line: t0 ? (t0.line || 0) : 0, t0Unres: Boolean(t0 && t0.unresolved),
          t0Draft: Boolean(t0 && t0.is_draft), t0Robot: Boolean(t0 && t0.is_robot),
          t0Author: t0 ? (t0.author_name || "") : "", t0Msg: t0 ? (t0.latest_message || t0.message || "") : "",
          t0MsgsLen: (t0 && Array.isArray(t0.messages)) ? t0.messages.length : 0,
          key: commentsViewKey
        };
      }
    }
    return outHtml;
  }

  function setPgCommentsViewFilter(clNum, filterName) {
    const num = Number(clNum || state.selectedClNumber || 0);
    state.commentsViewFilterByCl = state.commentsViewFilterByCl || {};
    state.commentsViewFilterByCl[num] = filterName;
    const pane = document.getElementById(`pgCommentsTabPane-${num}`);
    if (pane) {
      const c = state.changes.find(x => Number(x.number) === num);
      const bundle = state.diffsByCl && state.diffsByCl[num];
      if (c) pane.innerHTML = renderPgCommentsViewHtml(c, bundle);
    }
  }

  function onPgCommentsSearchInput(clNum, query) {
    const num = Number(clNum || state.selectedClNumber || 0);
    state.commentsViewQueryByCl = state.commentsViewQueryByCl || {};
    state.commentsViewQueryByCl[num] = query;
    const pane = document.getElementById(`pgCommentsTabPane-${num}`);
    if (pane) {
      const c = state.changes.find(x => Number(x.number) === num);
      const bundle = state.diffsByCl && state.diffsByCl[num];
      if (c) pane.innerHTML = renderPgCommentsViewHtml(c, bundle);
    }
  }

  function clearPgCommentsSearch(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    onPgCommentsSearchInput(num, "");
  }

  function togglePgCommentsViewCollapse(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    state.commentsViewCollapsedByCl = state.commentsViewCollapsedByCl || {};
    state.commentsViewCollapsedByCl[num] = !state.commentsViewCollapsedByCl[num];
    const pane = document.getElementById(`pgCommentsTabPane-${num}`);
    if (pane) {
      const c = state.changes.find(x => Number(x.number) === num);
      const bundle = state.diffsByCl && state.diffsByCl[num];
      if (c) pane.innerHTML = renderPgCommentsViewHtml(c, bundle);
    }
  }

  function parsePgLineHash(hashStr) {
    if (!hashStr) return null;
    let raw = String(hashStr).trim();
    if (raw.startsWith("#")) raw = raw.slice(1);
    if (!raw) return null;

    // PolyGerrit native format: /c/<cl>/[ps/]<filePath>@b?<line>(-b?<endLine>)?
    const polyGerritMatch = raw.match(/^\/?c\/\d+(?:\/\d+)?\/(.+?)@(b)?(?:L)?(\d+)(?:-(?:b)?(?:L)?(\d+))?$/i);
    if (polyGerritMatch) {
      let fpath = polyGerritMatch[1];
      try { fpath = decodeURIComponent(fpath); } catch (_) {}
      const startLine = Number(polyGerritMatch[3]);
      const endLine = polyGerritMatch[4] ? Number(polyGerritMatch[4]) : startLine;
      return {
        filePath: fpath,
        isBase: Boolean(polyGerritMatch[2]),
        side: polyGerritMatch[2] ? "left" : "right",
        line: startLine,
        startLine: startLine,
        endLine: Math.max(startLine, endLine),
      };
    }

    // File with colon: <filePath>:(b)?L?(\d+)(-(b)?L?(\d+))?
    const fileColonMatch = raw.match(/^(.+?):(b)?(?:L)?(\d+)(?:-(?:b)?(?:L)?(\d+))?$/i);
    if (fileColonMatch) {
      let fpath = fileColonMatch[1];
      try { fpath = decodeURIComponent(fpath); } catch (_) {}
      const startLine = Number(fileColonMatch[3]);
      const endLine = fileColonMatch[4] ? Number(fileColonMatch[4]) : startLine;
      return {
        filePath: fpath,
        isBase: Boolean(fileColonMatch[2]),
        side: fileColonMatch[2] ? "left" : "right",
        line: startLine,
        startLine: startLine,
        endLine: Math.max(startLine, endLine),
      };
    }

    // Simple line or range: (b)?L?(\d+)(-(b)?L?(\d+))?
    const simpleLineMatch = raw.match(/^(b)?(?:L)?(\d+)(?:-(?:b)?(?:L)?(\d+))?$/i);
    if (simpleLineMatch) {
      const startLine = Number(simpleLineMatch[2]);
      const endLine = simpleLineMatch[3] ? Number(simpleLineMatch[3]) : startLine;
      return {
        filePath: null,
        isBase: Boolean(simpleLineMatch[1]),
        side: simpleLineMatch[1] ? "left" : "right",
        line: startLine,
        startLine: startLine,
        endLine: Math.max(startLine, endLine),
      };
    }

    return null;
  }

  function buildPgDiffLineHash(filePath, lineNum, isBase = false, endLineNum = null) {
    const prefix = isBase ? "b" : "";
    const startStr = String(lineNum || 1);
    const endStr = (endLineNum != null && Number(endLineNum) > Number(lineNum)) ? `-${prefix}${endLineNum}` : "";
    if (filePath) {
      return `#${encodeURIComponent(filePath)}:${prefix}${startStr}${endStr}`;
    }
    return `#${prefix}${startStr}${endStr}`;
  }

  function buildPgDiffLinePermalink(clNum, filePath, lineNum, isBase = false, endLineNum = null) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const hash = buildPgDiffLineHash(filePath, lineNum, isBase, endLineNum);
    if (typeof window !== "undefined" && window.location) {
      const origin = window.location.origin || "";
      const pathname = window.location.pathname || "/";
      return `${origin}${pathname}?cl=${num}${hash}`;
    }
    return `?cl=${num}${hash}`;
  }

  function syncPgDiffLineHash(filePath, lineNum, isBase = false, endLineNum = null) {
    if (typeof window === "undefined" || !window.history || !window.location) return;
    const hash = buildPgDiffLineHash(filePath, lineNum, isBase, endLineNum);
    try {
      if (typeof window.history.replaceState === "function") {
        window.history.replaceState(null, "", hash);
      } else {
        window.location.hash = hash;
      }
    } catch (_) {}
  }

  function setActivePgLineElement(lineEl, isBase = false) {
    if (!lineEl) return;
    const root = (lineEl.closest && lineEl.closest(".diff-pane")) || document;
    if (root.querySelectorAll) {
      root.querySelectorAll(".code-line.pg-line-focused").forEach(el => {
        el.classList.remove("pg-line-focused");
        if (el.querySelectorAll) {
          el.querySelectorAll(".is-focused-side").forEach(s => s.classList.remove("is-focused-side"));
        }
      });
    }
    lineEl.classList.add("pg-line-focused");
    const targetGutter = isBase
      ? (lineEl.querySelector ? (lineEl.querySelector(".ln.ln-old") || lineEl.querySelector(".ln-old")) : null)
      : (lineEl.querySelector ? (lineEl.querySelector(".ln.ln-new") || lineEl.querySelector(".ln-new")) : null);
    if (targetGutter && targetGutter.classList) {
      targetGutter.classList.add("is-focused-side");
    }
    const targetCell = isBase
      ? (lineEl.querySelector ? lineEl.querySelector(".code-side-left") : null)
      : (lineEl.querySelector ? lineEl.querySelector(".code-side-right") : null);
    if (targetCell && targetCell.classList) {
      targetCell.classList.add("is-focused-side");
    }
  }

  function copyPgDiffLinePermalink(clNum, filePath, lineNum, isBase = false, endLineNum = null) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const url = buildPgDiffLinePermalink(num, filePath, lineNum, isBase, endLineNum);
    const sideLabel = isBase ? "Base" : "Revision";
    const baseName = filePath ? (filePath.split("/").pop() || filePath) : "diff";
    const isRange = endLineNum != null && Number(endLineNum) > Number(lineNum);
    const rangeSuffix = isRange ? `–${isBase ? "b" : ""}${endLineNum}` : "";
    const lineLabel = `${baseName}:${isBase ? "b" : ""}${lineNum}${rangeSuffix} (${sideLabel})`;

    state.activeLine = {
      clNum: num,
      filePath: filePath || "",
      lineNum: Number(lineNum),
      endLineNum: isRange ? Number(endLineNum) : Number(lineNum),
      isBase: Boolean(isBase),
      side: isBase ? "PARENT" : "REVISION",
    };
    if (typeof window !== "undefined" && window.state) {
      window.state.activeLine = state.activeLine;
    }
    syncPgDiffLineHash(filePath, lineNum, isBase, isRange ? endLineNum : null);

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(url).then(() => {
        if (typeof showToast === "function") {
          showToast(`📋 Copied permalink for ${lineLabel}`);
        }
      }).catch(() => {
        if (typeof showToast === "function") {
          showToast(`Permalink: ${url}`);
        }
      });
    } else if (typeof showToast === "function") {
      showToast(`Permalink: ${url}`);
    }
    return url;
  }

  function copyPgDiffRangePermalink(clNum, filePath, startLine, endLine, isBase = false) {
    return copyPgDiffLinePermalink(clNum, filePath, startLine, isBase, endLine);
  }

  function copyPgDiffActiveLinePermalink() {
    const sel = state.codeSelection || (typeof window !== "undefined" && window.state && window.state.codeSelection);
    if (sel && sel.filePath) {
      const isBase = sel.side === "PARENT";
      const startLine = (sel.startLine != null && Number(sel.startLine) > 0)
        ? Number(sel.startLine)
        : (sel.startIdx != null ? sel.startIdx + 1 : 1);
      const endLine = (sel.endLine != null && Number(sel.endLine) >= startLine)
        ? Number(sel.endLine)
        : startLine;
      return copyPgDiffLinePermalink(sel.clNum, sel.filePath, startLine, isBase, endLine > startLine ? endLine : null);
    }
    const al = state.activeLine || (typeof window !== "undefined" && window.state && window.state.activeLine);
    if (al && al.lineNum) {
      return copyPgDiffLinePermalink(al.clNum, al.filePath, al.lineNum, al.isBase, (al.endLineNum && al.endLineNum > al.lineNum) ? al.endLineNum : null);
    }
    const curBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
    if (curBlock) {
      const filePath = curBlock.getAttribute("data-file") || curBlock.getAttribute("data-file-path") || "";
      const clNum = Number(curBlock.getAttribute("data-cl") || state.selectedClNumber || 0);
      if (filePath && clNum) {
        return copyPgDiffLinePermalink(clNum, filePath, 1, false);
      }
    }
    if (typeof showToast === "function") {
      showToast("Click a diff line first to copy its permalink");
    }
    return null;
  }

  function getPgActiveLine() {
    return state.activeLine || (typeof window !== "undefined" && window.state && window.state.activeLine) || null;
  }

  function checkPgDiffLineHashAnchor() {
    if (typeof window === "undefined" || !window.location) return;
    const hash = window.location.hash || "";
    if (!hash || hash.startsWith("#message-")) return;

    const parsed = parsePgLineHash(hash);
    if (!parsed || !parsed.line) return;

    const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
    if (!num) return;

    scrollToPgCommentOrFile(num, parsed.filePath, parsed.line, null, parsed.isBase, {
      updateHash: false,
      endLine: parsed.endLine || parsed.line,
    });
  }

  async function scrollToPgCommentOrFile(clNum, filePath, line, commentId = null, isBase = false, opts = {}) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (typeof selectPgChangeTab === "function") {
      selectPgChangeTab(num, "files");
    }

    // If an active file filter hides this file, clear filters so it is visible
    if (typeof isPgFileFilterActive === "function" && isPgFileFilterActive(num)) {
      if (typeof clearAllPgFileFilters === "function") {
        clearAllPgFileFilters(num, false);
      }
    }

    let safeFile = filePath || state.activeFile || "";
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    const container = changeView || document;
    if (!safeFile && container) {
      const firstBlock = container.querySelector(`.pg-file-diff-block[data-cl="${num}"]`) || container.querySelector(".pg-file-diff-block");
      if (firstBlock) safeFile = firstBlock.getAttribute("data-file") || firstBlock.getAttribute("data-file-path") || "";
    }
    if (!safeFile && !commentId) return;

    const baseName = safeFile ? (safeFile.split("/").pop() || safeFile) : "diff";

    // Handle COMMIT_MSG special magic entry
    if (safeFile === "/COMMIT_MSG" || safeFile === "COMMIT_MSG") {
      const commitMsgPanel = document.getElementById(`commitMsgPanel-${num}`) || document.getElementById(`commitMessagePanel-${num}`) || document.querySelector(`.commit-msg-table[data-cl="${num}"]`);
      if (commitMsgPanel) {
        if (typeof uncollapseCommitMessage === "function") {
          uncollapseCommitMessage(num, true);
        }
        let targetEl = null;
        if (commentId) {
          const safeId = safeEscapeCss(String(commentId));
          targetEl = commitMsgPanel.querySelector(
            `#inlineItem-${safeId}, [data-inline-id="${safeId}"], [data-server-id="${safeId}"], [data-root-id="${safeId}"], [data-leaf-id="${safeId}"], [data-comment-id="${safeId}"]`
          );
        }
        if (!targetEl && line) {
          const lineStr = String(line);
          targetEl = commitMsgPanel.querySelector(`.diff-inline-comment[data-line="${lineStr}"], .commit-msg-line[data-line="${lineStr}"]`);
        }
        const commentCard = targetEl && (targetEl.classList.contains("diff-inline-comment")
          ? targetEl
          : (targetEl.querySelector(".diff-inline-comment") || (targetEl.nextElementSibling && targetEl.nextElementSibling.classList && targetEl.nextElementSibling.classList.contains("diff-inline-comment") ? targetEl.nextElementSibling : null)));
        const scrollTarget = commentCard || targetEl || commitMsgPanel;

        if (commentCard) {
          if (commentCard.classList.contains("collapsed")) {
            commentCard.classList.remove("collapsed");
            const toggleBtn = commentCard.querySelector(".inline-collapse-btn");
            if (toggleBtn) toggleBtn.textContent = "Hide ▴";
          }
          const idAttr = commentCard.getAttribute("data-inline-id");
          if (idAttr) state.collapsedInlineIds.delete(idAttr);
        }

        const isCodeLine = targetEl && targetEl.classList && (targetEl.classList.contains("code-line") || targetEl.classList.contains("commit-msg-line"));
        if (isCodeLine) {
          setActivePgLineElement(targetEl, false);
          state.activeLine = {
            clNum: num,
            filePath: "/COMMIT_MSG",
            lineNum: Number(line),
            endLineNum: (opts.endLine && Number(opts.endLine) > Number(line)) ? Number(opts.endLine) : Number(line),
            isBase: false,
            side: "REVISION",
          };
          if (typeof window !== "undefined" && window.state) {
            window.state.activeLine = state.activeLine;
          }
          if (opts.updateHash !== false) {
            syncPgDiffLineHash("/COMMIT_MSG", line, false, (opts.endLine && Number(opts.endLine) > Number(line)) ? Number(opts.endLine) : null);
          }
          if (opts.endLine && Number(opts.endLine) > Number(line)) {
            const tableEl = commitMsgPanel.querySelector(".code-table") || commitMsgPanel;
            if (tableEl && typeof selectCodeBlockRange === "function") {
              const startIdx = Number(targetEl.getAttribute("data-idx"));
              const endLnStr = String(opts.endLine);
              const endEl = tableEl.querySelector(`.commit-msg-line[data-start-ln="${endLnStr}"], .commit-msg-line[data-new-ln="${endLnStr}"]`);
              const endIdx = endEl ? Number(endEl.getAttribute("data-idx")) : startIdx;
              if (!Number.isNaN(startIdx) && !Number.isNaN(endIdx)) {
                selectCodeBlockRange(tableEl, startIdx, endIdx, {
                  anchorIdx: startIdx,
                  focusInput: false,
                  mountComposer: false,
                  side: "REVISION",
                });
              }
            }
          }
        }

        const doScroll = () => {
          if (typeof scrollTarget.scrollIntoView === "function") {
            scrollTarget.scrollIntoView({ behavior: "smooth", block: "center" });
          }
          scrollTarget.classList.add("flash-highlight");
          setTimeout(() => scrollTarget.classList.remove("flash-highlight"), 1800);
        };
        if (typeof requestAnimationFrame === "function") {
          requestAnimationFrame(() => requestAnimationFrame(doScroll));
        } else {
          doScroll();
        }

        if (typeof showToast === "function") {
          showToast(`Jumped to Commit Message${line ? ` line ${line}` : ""}`);
        }
        return;
      }
    }

    let fileBlock = Array.from(container.querySelectorAll(`.pg-file-diff-block[data-cl="${num}"]`))
      .find(b => b.getAttribute("data-file") === safeFile || b.getAttribute("data-file-path") === safeFile);
    if (!fileBlock) {
      fileBlock = Array.from(container.querySelectorAll(".pg-file-diff-block"))
        .find(b => b.getAttribute("data-file") === safeFile || b.getAttribute("data-file-path") === safeFile);
    }
    if (!fileBlock && !commentId) {
      fileBlock = (typeof getActivePgFileDiffBlock === "function" ? getActivePgFileDiffBlock() : null) ||
                  container.querySelector(`.pg-file-diff-block[data-cl="${num}"]`) ||
                  container.querySelector(".pg-file-diff-block");
    }

    if (fileBlock) {
      const ps = Number(fileBlock.getAttribute("data-ps") || 1);
      if (fileBlock.classList.contains("is-collapsed") && typeof setPgFileCollapsed === "function") {
        setPgFileCollapsed(num, ps, safeFile, false);
      }
      let targetEl = null;
      if (commentId) {
        const safeCommentId = safeEscapeCss(String(commentId));
        targetEl = fileBlock.querySelector(
          `#inlineItem-${safeCommentId}, [data-inline-id="${safeCommentId}"], [data-server-id="${safeCommentId}"], [data-root-id="${safeCommentId}"], [data-leaf-id="${safeCommentId}"], [data-comment-id="${safeCommentId}"]`
        );
      }
      const lineStr = String(line || "");
      if (!targetEl && line) {
        if (isBase) {
          targetEl = fileBlock.querySelector(`.code-line[data-old-ln="${lineStr}"]`);
        } else {
          targetEl = fileBlock.querySelector(`.code-line[data-new-ln="${lineStr}"]`);
        }
        if (!targetEl) {
          targetEl = fileBlock.querySelector(
            `.diff-inline-comment[data-line="${lineStr}"], .code-line[data-new-ln="${lineStr}"], .code-line[data-old-ln="${lineStr}"], .code-line[data-line="${lineStr}"]`
          );
        }
      }
      // If line is inside a collapsed context gap, expand that gap
      if (!targetEl && line && typeof expandPgDiffContext === "function") {
        const lineNum = Number(line);
        const contextBars = Array.from(fileBlock.querySelectorAll(".pg-context-bar"));
        for (const bar of contextBars) {
          const fromLn = Number(bar.getAttribute("data-from-line") || 0);
          const toLn = Number(bar.getAttribute("data-to-line") || 0);
          if (lineNum >= fromLn && lineNum <= toLn) {
            await expandPgDiffContext(null, num, safeFile, ps, fromLn, toLn, "all");
            if (commentId) {
              const safeCommentId = safeEscapeCss(String(commentId));
              targetEl = fileBlock.querySelector(
                `#inlineItem-${safeCommentId}, [data-inline-id="${safeCommentId}"], [data-server-id="${safeCommentId}"], [data-root-id="${safeCommentId}"], [data-leaf-id="${safeCommentId}"], [data-comment-id="${safeCommentId}"]`
              );
            }
            if (!targetEl) {
              if (isBase) {
                targetEl = fileBlock.querySelector(`.code-line[data-old-ln="${lineStr}"]`);
              } else {
                targetEl = fileBlock.querySelector(`.code-line[data-new-ln="${lineStr}"]`);
              }
              if (!targetEl) {
                targetEl = fileBlock.querySelector(
                  `.diff-inline-comment[data-line="${lineStr}"], .code-line[data-new-ln="${lineStr}"], .code-line[data-old-ln="${lineStr}"], .code-line[data-line="${lineStr}"]`
                );
              }
            }
            break;
          }
        }
      }

      if (targetEl) {
        // If target comment is hidden by author filter, unhide author filter
        if (targetEl.classList.contains("pg-author-hidden") && typeof getPgCommentsAuthorFilter === "function" && getPgCommentsAuthorFilter(num) !== "all") {
          if (typeof setPgCommentsAuthorFilter === "function") {
            setPgCommentsAuthorFilter(num, "all", false);
          }
        }
        // If target comment is resolved but resolved comments are hidden, unhide them
        if (targetEl.classList.contains("resolved") && typeof isPgShowResolvedCommentsEnabled === "function" && !isPgShowResolvedCommentsEnabled()) {
          if (typeof setPgShowResolvedCommentsEnabled === "function") {
            setPgShowResolvedCommentsEnabled(true);
          }
          if (typeof applyPgShowResolvedCommentsToDom === "function") {
            applyPgShowResolvedCommentsToDom(num, true);
          }
        }
        // If target comment is robot comment but robot comments are hidden, unhide them
        if (targetEl.classList.contains("is-robot") && typeof isPgShowRobotCommentsEnabled === "function" && !isPgShowRobotCommentsEnabled()) {
          if (typeof setPgShowRobotCommentsEnabled === "function") {
            setPgShowRobotCommentsEnabled(true);
          }
          if (typeof applyPgShowRobotCommentsToDom === "function") {
            applyPgShowRobotCommentsToDom(num, true);
          }
        }

        const isCodeLine = targetEl.classList && targetEl.classList.contains("code-line");
        if (isCodeLine) {
          setActivePgLineElement(targetEl, isBase);
          state.activeLine = {
            clNum: num,
            filePath: safeFile || targetEl.getAttribute("data-file") || "",
            lineNum: Number(line),
            endLineNum: (opts.endLine && Number(opts.endLine) > Number(line)) ? Number(opts.endLine) : Number(line),
            isBase: Boolean(isBase),
            side: isBase ? "PARENT" : "REVISION",
          };
          if (typeof window !== "undefined" && window.state) {
            window.state.activeLine = state.activeLine;
          }
          if (opts.updateHash !== false) {
            syncPgDiffLineHash(safeFile || targetEl.getAttribute("data-file") || "", line, isBase, (opts.endLine && Number(opts.endLine) > Number(line)) ? Number(opts.endLine) : null);
          }
          if (opts.endLine && Number(opts.endLine) > Number(line)) {
            const tableEl = targetEl.closest(".code-table");
            if (tableEl && typeof selectCodeBlockRange === "function") {
              const startIdx = Number(targetEl.getAttribute("data-idx"));
              const endLnStr = String(opts.endLine);
              const endEl = isBase
                ? (tableEl.querySelector(`.code-line[data-old-ln="${endLnStr}"]`) || tableEl.querySelector(`.code-line[data-line="${endLnStr}"]`))
                : (tableEl.querySelector(`.code-line[data-new-ln="${endLnStr}"]`) || tableEl.querySelector(`.code-line[data-line="${endLnStr}"]`));
              const endIdx = endEl ? Number(endEl.getAttribute("data-idx")) : startIdx;
              if (!Number.isNaN(startIdx) && !Number.isNaN(endIdx)) {
                selectCodeBlockRange(tableEl, startIdx, endIdx, {
                  anchorIdx: startIdx,
                  focusInput: false,
                  mountComposer: false,
                  side: isBase ? "PARENT" : "REVISION",
                });
              }
            }
          }
        }

        const commentCard = targetEl.classList.contains("diff-inline-comment")
          ? targetEl
          : (targetEl.querySelector(".diff-inline-comment") || (targetEl.nextElementSibling && targetEl.nextElementSibling.classList && targetEl.nextElementSibling.classList.contains("diff-inline-comment") ? targetEl.nextElementSibling : null));

        const scrollTarget = commentCard || targetEl;

        if (commentCard) {
          if (commentCard.classList.contains("collapsed")) {
            commentCard.classList.remove("collapsed");
            const toggleBtn = commentCard.querySelector(".inline-collapse-btn");
            if (toggleBtn) toggleBtn.textContent = "Hide ▴";
          }
          const idAttr = commentCard.getAttribute("data-inline-id");
          if (idAttr) {
            state.collapsedInlineIds.delete(idAttr);
          }
          if (typeof commentCard.focus === "function") {
            try { commentCard.focus({ preventScroll: true }); } catch (_) {}
          }
        }

        const doScroll = () => {
          if (typeof scrollTarget.scrollIntoView === "function") {
            scrollTarget.scrollIntoView({ behavior: "smooth", block: "center" });
          }
          scrollTarget.classList.add("flash-highlight");
          setTimeout(() => scrollTarget.classList.remove("flash-highlight"), 1800);
        };
        if (typeof requestAnimationFrame === "function") {
          requestAnimationFrame(() => requestAnimationFrame(doScroll));
        } else {
          doScroll();
        }

        if (typeof showToast === "function") {
          const sideNotice = isBase ? " (Base / Parent)" : "";
          const isRange = opts.endLine && Number(opts.endLine) > Number(line);
          const rangeLabel = isRange ? `${isBase ? "b" : ""}${line}–${isBase ? "b" : ""}${opts.endLine}` : `${isBase ? "b" : ""}${line}`;
          showToast(`Jumped to ${baseName}:${rangeLabel}${sideNotice}`);
        }
        return;
      }
      scrollToPgFileHeader(num, safeFile);
    } else {
      scrollToPgFileHeader(num, safeFile);
    }
  }

  function renderPgChangeLogSectionHtml(c, bundle) {
    const rawMsgs = (c && Array.isArray(c.messages))
      ? c.messages
      : ((bundle && Array.isArray(bundle.messages)) ? bundle.messages : []);
    const clNum = Number(c && c.number ? c.number : 0);
    if (rawMsgs.length === 0) {
      return `<section id="pgChangeLog-${clNum}" class="pg-change-log-section">
        <div class="pg-change-log-header">
          <div class="pg-change-log-title-area">
            <h3 class="pg-change-log-title">Change Log</h3>
            <span class="pg-change-log-count-badge">0 messages</span>
          </div>
        </div>
        <div class="pg-no-messages" style="padding:16px;color:var(--g-muted);font-size:12.5px;">No messages recorded for this change.</div>
      </section>`;
    }

    const hasHuman = rawMsgs.some(m => !m.is_bot);
    const hasBot = rawMsgs.some(m => Boolean(m.is_bot));
    let showAutomated = true;
    try {
      const saved = localStorage.getItem("pg_show_automated_messages");
      if (saved !== null) {
        showAutomated = saved === "1";
      } else {
        showAutomated = !hasHuman;
      }
    } catch (_) {}

    const activeCatsForCache = (state.msgCategoryFiltersByCl && state.msgCategoryFiltersByCl[clNum])
      ? state.msgCategoryFiltersByCl[clNum]
      : null;
    const catsSize = activeCatsForCache ? activeCatsForCache.size : 0;
    const localInlineLen = (state.inlineCommentsByCl && state.inlineCommentsByCl[clNum]) ? state.inlineCommentsByCl[clNum].length : 0;
    const srvThreadsLen = (bundle && Array.isArray(bundle.comment_threads)) ? bundle.comment_threads.length : ((bundle && Array.isArray(bundle.unresolved_threads)) ? bundle.unresolved_threads.length : 0);
    const robotLen = (bundle && Array.isArray(bundle.robot_comments)) ? bundle.robot_comments.length : 0;
    if (c && rawMsgs.length === 1 && catsSize === 0) {
      const m0 = rawMsgs[0];
      const clLogSub = c._clLogSub;
      if (
        clLogSub &&
        clLogSub.msgsRef === rawMsgs &&
        clLogSub.ps === (c.patch_set || 0) &&
        clLogSub.showAuto === showAutomated &&
        clLogSub.localInlineLen === localInlineLen &&
        clLogSub.srvThreadsLen === srvThreadsLen &&
        clLogSub.robotLen === robotLen &&
        m0 &&
        clLogSub.m0Id === (m0.id || 0) &&
        clLogSub.m0Ps === (m0.patch_set || 0) &&
        clLogSub.m0Author === (m0.author_name || "") &&
        clLogSub.m0Bot === Boolean(m0.is_bot) &&
        clLogSub.m0Tag === (m0.tag || "") &&
        clLogSub.m0Date === (m0.date || "") &&
        clLogSub.m0MsgLen === (m0.message || "").length &&
        c._cachedChangeLogKey === clLogSub.key &&
        typeof c._cachedChangeLogHtml === "string"
      ) {
        return c._cachedChangeLogHtml;
      }
    }
    const catsCacheSig = catsSize > 0 ? Array.from(activeCatsForCache).join(",") : "";
    let msgsCacheSig = "";
    for (let i = 0; i < rawMsgs.length; i++) {
      const m = rawMsgs[i];
      if (!m) continue;
      if (i > 0) msgsCacheSig += ";";
      msgsCacheSig += `${m.id || i}:${m.patch_set || 0}:${m.author_name || ""}:${m.is_bot ? 1 : 0}:${m.tag || ""}:${m.date || ""}:${(m.message || "").length}`;
    }
    const changeLogCacheKey = `${clNum}|${c && c.patch_set ? c.patch_set : 0}|${showAutomated ? 1 : 0}|${catsCacheSig}|${localInlineLen}:${srvThreadsLen}:${robotLen}|${msgsCacheSig}`;
    if (c && c._cachedChangeLogKey === changeLogCacheKey && typeof c._cachedChangeLogHtml === "string") {
      return c._cachedChangeLogHtml;
    }

    const psSet = new Set();
    if (c && c.patch_set) psSet.add(Number(c.patch_set));
    rawMsgs.forEach(m => {
      if (m.patch_set) psSet.add(Number(m.patch_set));
    });
    const sortedPs = Array.from(psSet).sort((a, b) => b - a);

    const authorMap = new Map();
    rawMsgs.forEach(m => {
      const name = String(m.author_name || "").trim() || "Unknown";
      if (!authorMap.has(name)) {
        authorMap.set(name, { name, count: 0, isBot: Boolean(m.is_bot) });
      }
      authorMap.get(name).count++;
    });
    const uniqueAuthors = Array.from(authorMap.values());

    const tagMap = new Map();
    rawMsgs.forEach(m => {
      const rawTag = String(m.tag || "").trim();
      if (rawTag) {
        if (!tagMap.has(rawTag)) {
          tagMap.set(rawTag, {
            tag: rawTag,
            label: (typeof formatPgMessageTag === "function") ? formatPgMessageTag(rawTag) : rawTag,
            count: 0,
          });
        }
        tagMap.get(rawTag).count++;
      }
    });
    const uniqueTags = Array.from(tagMap.values());

    const totalMsgs = rawMsgs.length;
    let humanCount = 0;
    let botCount = 0;
    let commentsCount = 0;
    let votesCount = 0;
    let attnCount = 0;
    let robotCount = 0;

    rawMsgs.forEach(m => {
      if (m.is_bot) botCount++;
      else humanCount++;
      const mComments = getPgCommentsForMessage(clNum, m, bundle);
      const hasComments = isPgMessageWithComments(m, clNum, bundle) || mComments.length > 0;
      if (hasComments) commentsCount++;
      if (isPgMessageWithVotes(m)) votesCount++;
      if (isPgMessageWithAttn(m)) attnCount++;
      const hasRobot = isPgMessageWithRobotComments(m, clNum, bundle) || mComments.some(c => Boolean(c.is_robot || c.robot_id));
      if (hasRobot) robotCount++;
    });

    const activeCats = (state.msgCategoryFiltersByCl && state.msgCategoryFiltersByCl[clNum])
      ? state.msgCategoryFiltersByCl[clNum]
      : new Set();
    const isAllActive = activeCats.size === 0;

    const cardsHtml = rawMsgs.map((m, idx) => {
      const isBot = Boolean(m.is_bot);
      const isLatest = idx === rawMsgs.length - 1;
      const isExpanded = hasHuman ? !isBot : isLatest;
      const initials = getPgAuthorInitials(m.author_name);
      const avatarHtml = isBot
        ? `<span class="pg-avatar-chip pg-avatar-bot" title="Automated Bot">🤖</span>`
        : `<span class="pg-avatar-chip" title="${escapeHtml(m.author_name)}">${escapeHtml(initials)}</span>`;
      const botTagHtml = isBot
        ? `<span class="pg-msg-bot-tag" title="${escapeHtml(m.tag || "Automated")}">Automated</span>`
        : "";
      const rawTag = String(m.tag || "").trim();
      const formattedTag = rawTag ? ((typeof formatPgMessageTag === "function") ? formatPgMessageTag(rawTag) : rawTag) : "";
      const tagChipHtml = rawTag
        ? `<span class="pg-msg-tag-chip" onclick="event.stopPropagation(); filterPgMessagesByTag(${clNum}, '${escapeJsQuote(rawTag)}')" title="Filter messages by tag: ${escapeHtml(rawTag)} · Click to filter">🏷️ ${escapeHtml(formattedTag)}</span>`
        : "";
      const rawMsgText = String(m.message || "");
      const isAttnUpdate = isPgMessageWithAttn(m);
      const attnTagHtml = isAttnUpdate
        ? `<span class="pg-msg-attn-tag" title="Attention set update recorded in this message">⚡ Attention</span>`
        : "";
      const msgComments = getPgCommentsForMessage(clNum, m, bundle);
      const hasComments = isPgMessageWithComments(m, clNum, bundle) || msgComments.length > 0;
      const hasRobot = isPgMessageWithRobotComments(m, clNum, bundle) || msgComments.some(c => Boolean(c.is_robot || c.robot_id));
      const robotTagHtml = hasRobot
        ? `<span class="pg-msg-robot-tag" title="Message contains robot comments / automated checks">🤖 Robot</span>`
        : "";
      const commentsListHtml = renderPgMessageCommentsListHtml(clNum, msgComments);
      const commentsTagHtml = hasComments && !isBot
        ? `<span class="pg-msg-comments-badge" title="Message contains comments, votes, or review feedback">💬 Review</span>`
        : "";
      const hasVotes = isPgMessageWithVotes(m);
      const votesTagHtml = hasVotes
        ? `<span class="pg-msg-votes-badge" title="Message contains review or verification votes">⭐ Vote</span>`
        : "";
      const psPillHtml = m.patch_set
        ? `<span class="pg-msg-ps-pill" title="Patchset ${m.patch_set}">PS ${m.patch_set}</span>`
        : "";
      const relDate = formatPgRelativeDate(m.date);
      const commentsSummaryHtml = renderPgMessageCommentsSummaryHtml(msgComments);
      const summarySnippet = computePgMessageSummarySnippet(m, msgComments);

      return `<div class="pg-message-card ${isBot ? "pg-msg-automated" : ""} ${hasRobot ? "pg-msg-has-robot" : ""} ${isExpanded ? "pg-msg-expanded" : "pg-msg-collapsed"}" id="pgMsg-${clNum}-${idx}" data-msg-idx="${idx}" data-msg-id="${escapeHtml(m.id || String(idx))}" data-ps="${m.patch_set || 0}" data-is-bot="${isBot ? "1" : "0"}" data-author="${escapeHtml(m.author_name || "")}" data-has-comments="${hasComments ? "1" : "0"}" data-has-robot="${hasRobot ? "1" : "0"}" data-has-votes="${hasVotes ? "1" : "0"}" data-has-attn="${isAttnUpdate ? "1" : "0"}" data-tag="${escapeHtml(m.tag || "")}">
        <div class="pg-message-header" onclick="togglePgMessageCard(event, ${clNum}, ${idx})">
          <div class="pg-msg-header-left">
            <span class="pg-msg-chevron">${isExpanded ? "▼" : "▶"}</span>
            ${avatarHtml}
            <span class="pg-msg-author">${escapeHtml(m.author_name)}</span>
            ${botTagHtml}
            ${robotTagHtml}
            ${tagChipHtml}
            ${attnTagHtml}
            ${commentsTagHtml}
            ${votesTagHtml}
            ${psPillHtml}
            ${commentsSummaryHtml}
            <span class="pg-msg-summary-snippet">${escapeHtml(summarySnippet)}</span>
          </div>
          <div class="pg-msg-header-right">
            <button type="button" class="pg-btn-copy-msg-text" onclick="copyPgMessageText(event, ${clNum}, ${idx})" title="Copy message text to clipboard">📋</button>
            <button type="button" class="pg-btn-quote-msg" onclick="quotePgMessage(event, ${clNum}, ${idx})" title="Quote message in Reply dialog (Shortcut: q)">💬 Quote</button>
            <button type="button" class="pg-btn-copy-msg-link" onclick="copyPgMessageLink(event, ${clNum}, ${idx}, '${escapeHtml(m.id || "")}')" title="Copy link to this message">🔗</button>
            <span class="pg-msg-date" title="${escapeHtml(m.date || "")}">${escapeHtml(relDate)}</span>
          </div>
        </div>
        <div class="pg-message-body">
          ${formatPgMessageContentHtml(m.message)}
          ${commentsListHtml}
          <div class="pg-msg-actions">
            <button type="button" class="pg-btn-msg-action pg-btn-msg-quote" onclick="quotePgMessage(event, ${clNum}, ${idx})" title="Quote message or selection in Reply dialog (q)">💬 Quote (q)</button>
            <button type="button" class="pg-btn-msg-action pg-btn-msg-copy" onclick="copyPgMessageText(event, ${clNum}, ${idx})" title="Copy message text to clipboard">📋 Copy Text</button>
          </div>
        </div>
      </div>`;
    }).join("");

    const outHtml = `<section id="pgChangeLog-${clNum}" class="pg-change-log-section">
      <div class="pg-change-log-header">
        <div class="pg-change-log-top-row">
          <div class="pg-change-log-title-area">
            <h3 class="pg-change-log-title">Change Log</h3>
            <span class="pg-change-log-count-badge">${rawMsgs.length} messages</span>
            <button type="button" id="btnPgClearMsgFilters-${clNum}" class="pg-btn-clear-msg-filters" onclick="clearAllPgMessageFilters(${clNum})" title="Reset all message filters" style="display:none;">Clear filters</button>
          </div>
          <div class="pg-change-log-controls">
            <div class="pg-msg-search-wrap">
              <span class="pg-msg-search-icon">🔍</span>
              <input type="text" id="pgMsgFilterSearch-${clNum}" class="pg-msg-search-input" placeholder="Filter messages..." oninput="onPgMessageSearchInput(${clNum}, this.value)" title="Filter messages by text, author, tag, or PS">
              <button type="button" id="btnPgMsgSearchClear-${clNum}" class="pg-msg-search-clear" onclick="clearPgMessageSearch(${clNum})" title="Clear search filter" style="display:none;">✕</button>
            </div>
            <select id="pgMsgFilterAuthor-${clNum}" class="pg-msg-author-select" onchange="onPgMessageAuthorFilterChange(${clNum}, this.value)" title="Filter messages by author/reviewer">
              <option value="all">All Authors</option>
              ${hasHuman && hasBot ? `<option value="human">Human Reviewers Only</option>` : ""}
              ${uniqueAuthors.map(a => `<option value="${escapeHtml(a.name)}">${escapeHtml(a.name)} (${a.count})</option>`).join("")}
            </select>
            ${uniqueTags.length > 0 ? `<select id="pgMsgFilterTag-${clNum}" class="pg-msg-tag-select" onchange="onPgMessageTagFilterChange(${clNum}, this.value)" title="Filter messages by tag">
              <option value="all">All Tags (${uniqueTags.reduce((sum, t) => sum + t.count, 0)})</option>
              ${uniqueTags.map(t => `<option value="${escapeHtml(t.tag)}">${escapeHtml(t.label)} (${t.count})</option>`).join("")}
            </select>` : ""}
            <label class="pg-filter-checkbox-label" title="Show only messages containing comments, votes, or review feedback">
              <input type="checkbox" id="pgMsgFilterCommentsOnly-${clNum}" onchange="togglePgMessageCommentsOnly(${clNum})">
              <span>Comments only</span>
            </label>
            <label class="pg-filter-checkbox-label" title="Show or hide automated CI/bot messages">
              <input type="checkbox" id="pgMsgFilterAuto-${clNum}" onchange="togglePgAutomatedMessages(${clNum})" ${showAutomated ? "checked" : ""}>
              <span id="pgMsgFilterAutoLabel-${clNum}">Automatically generated (${botCount})</span>
            </label>
            <select id="pgMsgFilterPs-${clNum}" class="pg-msg-ps-select" onchange="onPgMessagePsFilterChange(${clNum}, this.value)" title="Filter messages by patch set">
              <option value="all">All Patch Sets</option>
              ${sortedPs.map(p => `<option value="${p}">Patchset ${p}</option>`).join("")}
            </select>
            <button type="button" class="pg-btn-subtle" onclick="toggleAllPgMessages(${clNum})" title="Toggle expand / collapse all messages">Toggle All</button>
            <button type="button" class="pg-btn-subtle pg-btn-expand-all-messages" id="btnExpandAllMessages-${clNum}" onclick="expandAllPgMessages(${clNum})" title="Expand all message cards (Shortcut: x)">Expand All <span class="pg-shortcut-hint">x</span></button>
            <button type="button" class="pg-btn-subtle pg-btn-collapse-all-messages" id="btnCollapseAllMessages-${clNum}" onclick="collapseAllPgMessages(${clNum})" title="Collapse all message cards (Shortcut: z)">Collapse All <span class="pg-shortcut-hint">z</span></button>
          </div>
        </div>
        <div class="pg-change-log-chips-row">
          <div class="pg-msg-filter-chips" id="pgMsgFilterChips-${clNum}" role="group" aria-label="Message category filter chips">
            <button type="button" class="pg-msg-chip ${isAllActive ? "is-active" : ""}" data-cat="all" onclick="togglePgMessageCategoryFilter(${clNum}, 'all')" aria-pressed="${isAllActive ? "true" : "false"}" title="Show all messages">All <span class="pg-chip-count">${totalMsgs}</span></button>
            <button type="button" class="pg-msg-chip ${activeCats.has("human") ? "is-active" : ""}" data-cat="human" onclick="togglePgMessageCategoryFilter(${clNum}, 'human')" aria-pressed="${activeCats.has("human") ? "true" : "false"}" title="Show messages by human reviewers">👤 Human <span class="pg-chip-count">${humanCount}</span></button>
            <button type="button" class="pg-msg-chip ${activeCats.has("comments") ? "is-active" : ""}" data-cat="comments" onclick="togglePgMessageCategoryFilter(${clNum}, 'comments')" aria-pressed="${activeCats.has("comments") ? "true" : "false"}" title="Show messages containing comments or review feedback">💬 Comments <span class="pg-chip-count">${commentsCount}</span></button>
            ${robotCount > 0 ? `<button type="button" class="pg-msg-chip ${activeCats.has("robot") ? "is-active" : ""}" data-cat="robot" onclick="togglePgMessageCategoryFilter(${clNum}, 'robot')" aria-pressed="${activeCats.has("robot") ? "true" : "false"}" title="Show messages containing robot comments or automated checks">🤖 Robot <span class="pg-chip-count">${robotCount}</span></button>` : ""}
            <button type="button" class="pg-msg-chip ${activeCats.has("votes") ? "is-active" : ""}" data-cat="votes" onclick="togglePgMessageCategoryFilter(${clNum}, 'votes')" aria-pressed="${activeCats.has("votes") ? "true" : "false"}" title="Show messages containing review or verification votes">⭐ Votes <span class="pg-chip-count">${votesCount}</span></button>
            <button type="button" class="pg-msg-chip ${activeCats.has("attention") ? "is-active" : ""}" data-cat="attention" onclick="togglePgMessageCategoryFilter(${clNum}, 'attention')" aria-pressed="${activeCats.has("attention") ? "true" : "false"}" title="Show messages with attention set updates">⚡ Attention <span class="pg-chip-count">${attnCount}</span></button>
            <button type="button" class="pg-msg-chip ${activeCats.has("automated") ? "is-active" : ""}" data-cat="automated" onclick="togglePgMessageCategoryFilter(${clNum}, 'automated')" aria-pressed="${activeCats.has("automated") ? "true" : "false"}" title="Show automated bot / CI messages">🤖 Automated <span class="pg-chip-count">${botCount}</span></button>
          </div>
        </div>
      </div>
      <div class="pg-messages-list">
        ${cardsHtml}
      </div>
    </section>`;
    if (c) {
      c._cachedChangeLogKey = changeLogCacheKey;
      c._cachedChangeLogHtml = outHtml;
      if (rawMsgs.length === 1 && catsSize === 0) {
        const m0 = rawMsgs[0];
        if (m0) {
          c._clLogSub = {
            msgsRef: rawMsgs, ps: c.patch_set || 0, showAuto: showAutomated,
            localInlineLen, srvThreadsLen, robotLen,
            m0Id: m0.id || 0, m0Ps: m0.patch_set || 0, m0Author: m0.author_name || "",
            m0Bot: Boolean(m0.is_bot), m0Tag: m0.tag || "", m0Date: m0.date || "",
            m0MsgLen: (m0.message || "").length, key: changeLogCacheKey
          };
        }
      }
    }
    return outHtml;
  }

  if (typeof window !== "undefined") {
    window.formatPgRelativeDate = formatPgRelativeDate;
    window.formatPgMessageTag = formatPgMessageTag;
    window.onPgMessageTagFilterChange = onPgMessageTagFilterChange;
    window.filterPgMessagesByTag = filterPgMessagesByTag;
    window.clearPgMessageTagFilter = clearPgMessageTagFilter;
    window.getPgAuthorInitials = getPgAuthorInitials;
    window.formatPgMessageContentHtml = formatPgMessageContentHtml;
    window.isPgMessageWithComments = isPgMessageWithComments;
    window.isPgMessageWithRobotComments = isPgMessageWithRobotComments;
    window.highlightPgSearchText = highlightPgSearchText;
    window.onPgMessageSearchInput = onPgMessageSearchInput;
    window.clearPgMessageSearch = clearPgMessageSearch;
    window.onPgMessageAuthorFilterChange = onPgMessageAuthorFilterChange;
    window.togglePgMessageCommentsOnly = togglePgMessageCommentsOnly;
    window.clearAllPgMessageFilters = clearAllPgMessageFilters;
    window.copyPgMessageLink = copyPgMessageLink;
    window.checkPgMessageHashAnchor = checkPgMessageHashAnchor;
    window.formatPgQuoteText = formatPgQuoteText;
    window.getPgMessageByIndex = getPgMessageByIndex;
    window.quotePgMessage = quotePgMessage;
    window.copyPgMessageText = copyPgMessageText;
    window.quoteInlineCommentInReply = quoteInlineCommentInReply;
    window.togglePgMessageCard = togglePgMessageCard;
    window.expandAllPgMessages = expandAllPgMessages;
    window.collapseAllPgMessages = collapseAllPgMessages;
    window.togglePgAutomatedMessages = togglePgAutomatedMessages;
    window.onPgMessagePsFilterChange = onPgMessagePsFilterChange;
    window.applyPgMessageFilters = applyPgMessageFilters;
    window.isPgMessageWithVotes = isPgMessageWithVotes;
    window.isPgMessageWithAttn = isPgMessageWithAttn;
    window.getPgMessageCategoryFilters = getPgMessageCategoryFilters;
    window.syncPgMessageFilterChipsDom = syncPgMessageFilterChipsDom;
    window.togglePgMessageCategoryFilter = togglePgMessageCategoryFilter;
    window.toggleAllPgMessages = toggleAllPgMessages;
    window.scrollToChangeLog = scrollToChangeLog;
    window.escapeJsQuote = escapeJsQuote;
    window.parsePgCommentsFromMessageText = parsePgCommentsFromMessageText;
    window.getAllPgCommentsForCl = getAllPgCommentsForCl;
    window.getPgCommentsForMessage = getPgCommentsForMessage;
    window.renderPgMessageCommentsListHtml = renderPgMessageCommentsListHtml;
    window.computePgMessageSummarySnippet = computePgMessageSummarySnippet;
    window.renderPgMessageCommentsSummaryHtml = renderPgMessageCommentsSummaryHtml;
    window.renderPgCommentsViewHtml = renderPgCommentsViewHtml;
    window.setPgCommentsViewFilter = setPgCommentsViewFilter;
    window.getPgCommentAuthors = getPgCommentAuthors;
    window.getPgCommentsAuthorFilter = getPgCommentsAuthorFilter;
    window.setPgCommentsAuthorFilter = setPgCommentsAuthorFilter;
    window.clearPgCommentsAuthorFilter = clearPgCommentsAuthorFilter;
    window.cyclePgCommentsAuthorFilter = cyclePgCommentsAuthorFilter;
    window.togglePgDiffAuthorFilter = togglePgDiffAuthorFilter;
    window.applyPgCommentsAuthorFilterToDom = applyPgCommentsAuthorFilterToDom;
    window.onPgCommentsSearchInput = onPgCommentsSearchInput;
    window.clearPgCommentsSearch = clearPgCommentsSearch;
    window.togglePgCommentsViewCollapse = togglePgCommentsViewCollapse;
    window.scrollToPgCommentOrFile = scrollToPgCommentOrFile;
    window.parsePgLineHash = parsePgLineHash;
    window.buildPgDiffLineHash = buildPgDiffLineHash;
    window.buildPgDiffLinePermalink = buildPgDiffLinePermalink;
    window.syncPgDiffLineHash = syncPgDiffLineHash;
    window.setActivePgLineElement = setActivePgLineElement;
    window.copyPgDiffLinePermalink = copyPgDiffLinePermalink;
    window.copyPgDiffRangePermalink = copyPgDiffRangePermalink;
    window.copyPgDiffActiveLinePermalink = copyPgDiffActiveLinePermalink;
    window.getPgActiveLine = getPgActiveLine;
    window.checkPgDiffLineHashAnchor = checkPgDiffLineHashAnchor;
    window.onDiffLineGutterContextMenu = onDiffLineGutterContextMenu;
    window.renderPgChangeLogSectionHtml = renderPgChangeLogSectionHtml;
    window.renderPgContextControlBarHtml = renderPgContextControlBarHtml;
    window.renderPgTrailingContextControlBarHtml = renderPgTrailingContextControlBarHtml;
    window.renderPgHunkExpandActionsHtml = renderPgHunkExpandActionsHtml;
    window.onPgHunkHeaderKeyDown = onPgHunkHeaderKeyDown;
    window.onPgContextBarClick = onPgContextBarClick;
    window.onPgContextBarKeyDown = onPgContextBarKeyDown;
    window.expandPgDiffContext = expandPgDiffContext;
    window.expandAllVisibleDiffContext = expandAllVisibleDiffContext;
    window.isPgFileContextExpanded = isPgFileContextExpanded;
    window.expandPgFileDiffContext = expandPgFileDiffContext;
    window.restorePgFileDiffHunks = restorePgFileDiffHunks;
    window.togglePgFileContext = togglePgFileContext;
  }

  function onPgContextBarClick(event, clNum, safeFile, patchSet, fromLine, toLine) {
    if (!event) return;
    const target = event.target;
    if (target && target.closest && target.closest(".pg-btn-context-expand")) {
      return;
    }
    const hasKnownTo = typeof toLine === "number" && toLine > 0;
    const skippedCount = hasKnownTo ? (toLine - fromLine + 1) : 100;
    const prefs = getPgDiffPreferences();
    const contextStep = prefs.context === "all" ? 10000 : (Number(prefs.context) || 10);
    const expandFn = (typeof window !== "undefined" && typeof window.expandPgDiffContext === "function") ? window.expandPgDiffContext : expandPgDiffContext;
    if (hasKnownTo && skippedCount <= contextStep) {
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "all");
    }

    let chunkSize = contextStep;
    if (event.altKey) {
      chunkSize = Math.min(100, skippedCount);
    } else if (event.shiftKey) {
      chunkSize = Math.min(50, skippedCount);
    }

    let mode = "below";
    const barEl = (target && target.closest && target.closest(".pg-context-bar")) || (event.currentTarget && event.currentTarget.closest && event.currentTarget.closest(".pg-context-bar")) || event.currentTarget;
    const isTrailing = barEl && barEl.classList && barEl.classList.contains("pg-trailing-context-bar");
    if (!isTrailing && barEl && typeof barEl.getBoundingClientRect === "function" && typeof event.clientX === "number" && event.clientX > 0) {
      const rect = barEl.getBoundingClientRect();
      const clickRelX = (event.clientX - rect.left) / (rect.width || 1);
      if (clickRelX > 0.55) {
        mode = "above";
      } else if (clickRelX < 0.45) {
        mode = "below";
      } else {
        return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "all");
      }
    }

    return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, mode, chunkSize);
  }

  function onPgContextBarKeyDown(event, clNum, safeFile, patchSet, fromLine, toLine) {
    if (!event) return;
    const key = event.key;
    const hasKnownTo = typeof toLine === "number" && toLine > 0;
    const skippedCount = hasKnownTo ? (toLine - fromLine + 1) : 100;
    const expandFn = (typeof window !== "undefined" && typeof window.expandPgDiffContext === "function") ? window.expandPgDiffContext : expandPgDiffContext;
    if (key === "Enter" || key === " " || key === "Spacebar" || key === "Home") {
      event.preventDefault();
      event.stopPropagation();
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "all");
    }

    const prefs = getPgDiffPreferences();
    const defaultStep = prefs.context === "all" ? skippedCount : (Number(prefs.context) || 10);
    const step = event.altKey ? Math.min(100, skippedCount) : (event.shiftKey ? Math.min(50, skippedCount) : defaultStep);

    if (key === "ArrowDown" || key === "j" || key === "PageDown") {
      event.preventDefault();
      event.stopPropagation();
      const count = key === "PageDown" ? Math.min(50, skippedCount) : step;
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "below", count);
    }
    if (key === "ArrowUp" || key === "k" || key === "PageUp") {
      event.preventDefault();
      event.stopPropagation();
      const count = key === "PageUp" ? Math.min(50, skippedCount) : step;
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "above", count);
    }
    if (key === "+" || key === "=") {
      event.preventDefault();
      event.stopPropagation();
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "below", step);
    }
    if (key === "x" || key === "X") {
      event.preventDefault();
      event.stopPropagation();
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "all");
    }
  }

  function renderPgHunkExpandActionsHtml(fallbackCl, safeFile, ps, fromLine, toLine, oldRange = "", newRange = "") {
    const clNum = Number(fallbackCl || 0);
    const patchSet = Number(ps || 1);
    const jsSafeFile = escapeJsQuote(safeFile);
    const from = Number(fromLine) || 0;
    const to = Number(toLine) || 0;
    const skippedCount = (from > 0 && to >= from) ? (to - from + 1) : 0;

    let buttonsHtml = "";
    if (skippedCount > 0) {
      const prefs = getPgDiffPreferences();
      const defaultStep = prefs.context === "all" ? skippedCount : (Number(prefs.context) || 10);
      const step1 = Math.min(defaultStep, skippedCount);
      const step2 = Math.min(25, skippedCount);
      const step3 = Math.min(50, skippedCount);

      const btn1 = `<button type="button" class="pg-btn-hunk-expand pg-btn-expand-above" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${from}, ${to}, 'above', ${step1})" title="Expand ${step1} lines above this hunk">▲ +${step1}</button>`;
      const btn2 = (step2 > step1 && skippedCount >= 20)
        ? `<button type="button" class="pg-btn-hunk-expand pg-btn-expand-above pg-btn-expand-step" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${from}, ${to}, 'above', ${step2})" title="Expand ${step2} lines above this hunk">▲ +${step2}</button>`
        : "";
      const btn3 = (step3 > step2 && skippedCount >= 45)
        ? `<button type="button" class="pg-btn-hunk-expand pg-btn-expand-above pg-btn-expand-step" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${from}, ${to}, 'above', ${step3})" title="Expand ${step3} lines above this hunk">▲ +${step3}</button>`
        : "";
      const btnAll = skippedCount > step1
        ? `<button type="button" class="pg-btn-hunk-expand pg-btn-expand-all" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${from}, ${to}, 'all')" title="Expand all ${skippedCount} skipped lines above this hunk (Shortcut: Shift+X)">▲ All (${skippedCount})</button>`
        : "";
      buttonsHtml = `<div class="pg-hunk-btn-group" role="group" aria-label="Expand context lines above hunk">${btn1}${btn2}${btn3}${btnAll}</div>`;
    }

    const badgeText = [oldRange, newRange].filter(Boolean).join(" ");
    const badgeHtml = badgeText ? `<span class="hunk-line-count-badge" title="Diff hunk location">${escapeHtml(badgeText)}</span>` : "";
    return `<div class="hunk-expand-actions" data-file="${safeFile}" data-from-line="${from}" data-to-line="${to}">${buttonsHtml}${badgeHtml}</div>`;
  }

  function onPgHunkHeaderKeyDown(event, clNum, safeFile, patchSet) {
    if (!event) return;
    const hunkEl = (event.currentTarget || event.target) && (typeof event.currentTarget.closest === "function" ? event.currentTarget.closest(".code-hunk") : event.currentTarget);
    if (!hunkEl) return;
    const fromLine = parseInt(hunkEl.getAttribute("data-from-line") || "0", 10);
    const toLine = parseInt(hunkEl.getAttribute("data-to-line") || "0", 10);
    if (fromLine <= 0 || toLine < fromLine) return;

    const key = event.key;
    const expandFn = (typeof window !== "undefined" && typeof window.expandPgDiffContext === "function") ? window.expandPgDiffContext : expandPgDiffContext;
    if (key === "Enter" || key === " " || key === "Spacebar" || key === "x" || key === "X") {
      event.preventDefault();
      event.stopPropagation();
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "all");
    }
    if (key === "ArrowUp" || key === "k" || key === "+" || key === "=") {
      event.preventDefault();
      event.stopPropagation();
      const skippedCount = toLine - fromLine + 1;
      const step = event.altKey ? Math.min(50, skippedCount) : (event.shiftKey ? Math.min(25, skippedCount) : Math.min(10, skippedCount));
      return expandFn(event, clNum, safeFile, patchSet, fromLine, toLine, "above", step);
    }
  }

  function renderPgContextControlBarHtml(fallbackCl, safeFile, ps, fromLine, toLine, isSbs) {
    const skippedCount = toLine - fromLine + 1;
    if (skippedCount <= 0) return "";
    const sbsCls = isSbs ? "code-line-sbs" : "";
    const clNum = Number(fallbackCl || 0);
    const patchSet = Number(ps || 1);
    const prefs = getPgDiffPreferences();
    const contextStep = prefs.context === "all" ? 10000 : (Number(prefs.context) || 10);
    const showExpandButtons = skippedCount > contextStep;
    const jsSafeFile = escapeJsQuote(safeFile);

    let btnAboveHtml = "";
    let btnBelowHtml = "";
    if (showExpandButtons) {
      const step1 = contextStep;
      const step2 = Math.min(50, skippedCount);
      const hasStep2 = skippedCount >= 40 && step2 > step1;
      const step3 = Math.min(100, skippedCount);
      const hasStep3 = skippedCount >= 90 && step3 > step2;

      const step1Below = `<button type="button" class="pg-btn-context-expand pg-btn-expand-below" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'below', ${step1})" title="Expand ${step1} lines below the preceding hunk">▼ +${step1} lines</button>`;
      const step2Below = hasStep2
        ? `<button type="button" class="pg-btn-context-expand pg-btn-expand-below pg-btn-expand-step" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'below', ${step2})" title="Expand ${step2} lines below the preceding hunk">▼ +${step2}</button>`
        : "";
      const step3Below = hasStep3
        ? `<button type="button" class="pg-btn-context-expand pg-btn-expand-below pg-btn-expand-step pg-btn-expand-chunk-100" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'below', ${step3})" title="Expand ${step3} lines below the preceding hunk">▼ +${step3}</button>`
        : "";
      btnBelowHtml = `${step1Below}${step2Below}${step3Below}`;

      const step3Above = hasStep3
        ? `<button type="button" class="pg-btn-context-expand pg-btn-expand-above pg-btn-expand-step pg-btn-expand-chunk-100" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'above', ${step3})" title="Expand ${step3} lines above this hunk">▲ +${step3}</button>`
        : "";
      const step2Above = hasStep2
        ? `<button type="button" class="pg-btn-context-expand pg-btn-expand-above pg-btn-expand-step" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'above', ${step2})" title="Expand ${step2} lines above this hunk">▲ +${step2}</button>`
        : "";
      const step1Above = `<button type="button" class="pg-btn-context-expand pg-btn-expand-above" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'above', ${step1})" title="Expand ${step1} lines above this hunk">▲ +${step1} lines</button>`;
      btnAboveHtml = `${step3Above}${step2Above}${step1Above}`;
    }

    const labelText = skippedCount === 1 ? "1 line skipped" : `${skippedCount} lines skipped`;
    const btnAllText = skippedCount <= contextStep ? `Show all (${skippedCount} lines)` : `Expand all (${skippedCount} lines)`;

    return `<div class="code-line code-context-control pg-context-bar ${sbsCls}" data-cl="${clNum}" data-file="${safeFile}" data-ps="${patchSet}" data-from-line="${fromLine}" data-to-line="${toLine}" tabindex="0" role="region" aria-label="${labelText}" onclick="onPgContextBarClick(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine})" ondblclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'all')" onkeydown="onPgContextBarKeyDown(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine})" title="Click to expand context (click left: below, right: above, Shift: +50, Alt: +100) · Double-click or press Enter to expand all ${skippedCount} skipped lines (Shortcut: Shift+X)">
      <div class="pg-context-controls-inner">
        <span class="pg-context-label">
          <span class="pg-context-icon" aria-hidden="true">↕</span>
          <span class="pg-context-text">${labelText}</span>
        </span>
        <div class="pg-context-btn-group">
          ${btnBelowHtml}
          <button type="button" class="pg-btn-context-expand pg-btn-expand-all" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${toLine}, 'all')" title="Expand all ${skippedCount} skipped lines (Shortcut: Shift+X)">
            ${btnAllText}
          </button>
          ${btnAboveHtml}
        </div>
      </div>
    </div>`;
  }

  function renderPgTrailingContextControlBarHtml(fallbackCl, safeFile, ps, fromLine, toLine, isSbs = false) {
    const clNum = Number(fallbackCl || 0);
    const patchSet = Number(ps || 1);
    const sbsCls = isSbs ? "code-line-sbs" : "";
    const jsSafeFile = escapeJsQuote(safeFile);
    const hasKnownTo = typeof toLine === "number" && toLine > 0;
    const skippedCount = hasKnownTo ? Math.max(0, toLine - fromLine + 1) : -1;
    if (hasKnownTo && skippedCount <= 0) return "";

    const prefs = getPgDiffPreferences();
    const contextStep = prefs.context === "all" ? 10000 : (Number(prefs.context) || 10);
    const effectiveTo = hasKnownTo ? toLine : -1;

    let btnBelowHtml = "";
    if (!hasKnownTo || skippedCount > contextStep) {
      const step1 = contextStep;
      const hasStep2 = !hasKnownTo || (skippedCount >= 40);
      const step2 = hasKnownTo ? Math.min(50, skippedCount) : 50;
      const hasStep3 = !hasKnownTo || (skippedCount >= 90);
      const step3 = hasKnownTo ? Math.min(100, skippedCount) : 100;

      const step1Below = `<button type="button" class="pg-btn-context-expand pg-btn-expand-below" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${effectiveTo}, 'below', ${step1})" title="Expand ${step1} lines below to EOF">▼ +${step1} lines</button>`;
      const step2Below = hasStep2
        ? `<button type="button" class="pg-btn-context-expand pg-btn-expand-below pg-btn-expand-step" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${effectiveTo}, 'below', ${step2})" title="Expand ${step2} lines below to EOF">▼ +${step2}</button>`
        : "";
      const step3Below = hasStep3
        ? `<button type="button" class="pg-btn-context-expand pg-btn-expand-below pg-btn-expand-step pg-btn-expand-chunk-100" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${effectiveTo}, 'below', ${step3})" title="Expand ${step3} lines below to EOF">▼ +${step3}</button>`
        : "";
      btnBelowHtml = `${step1Below}${step2Below}${step3Below}`;
    }

    const labelText = hasKnownTo
      ? (skippedCount === 1 ? "1 line skipped to EOF" : `${skippedCount} lines skipped to EOF`)
      : "Lines skipped to EOF";
    const btnAllText = hasKnownTo
      ? (skippedCount <= contextStep ? `Show all to EOF (${skippedCount} lines)` : `Expand all to EOF (${skippedCount} lines)`)
      : "Expand all to EOF";

    return `<div class="code-line code-context-control pg-context-bar pg-trailing-context-bar ${sbsCls}" data-cl="${clNum}" data-file="${safeFile}" data-ps="${patchSet}" data-from-line="${fromLine}" data-to-line="${effectiveTo}" data-trailing="true" tabindex="0" role="region" aria-label="${labelText}" onclick="onPgContextBarClick(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${effectiveTo})" ondblclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${effectiveTo}, 'all')" onkeydown="onPgContextBarKeyDown(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${effectiveTo})" title="Click to expand trailing context lines below to EOF (Shift: +50, Alt: +100) · Double-click or press Enter to expand all to EOF (Shortcut: Shift+X)">
      <div class="pg-context-controls-inner">
        <span class="pg-context-label">
          <span class="pg-context-icon" aria-hidden="true">▼</span>
          <span class="pg-context-text">${labelText}</span>
          <span class="pg-trailing-eof-badge">EOF</span>
        </span>
        <div class="pg-context-btn-group">
          ${btnBelowHtml}
          <button type="button" class="pg-btn-context-expand pg-btn-expand-all" onclick="expandPgDiffContext(event, ${clNum}, '${jsSafeFile}', ${patchSet}, ${fromLine}, ${effectiveTo}, 'all')" title="Expand all lines to EOF (Shortcut: Shift+X)">
            ${btnAllText}
          </button>
        </div>
      </div>
    </div>`;
  }

  async function expandPgDiffContext(event, clNum, filePath, ps, fromLine, toLine, mode, count = null) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const btn = event && (event.currentTarget || event.target);
    let barEl = btn && typeof btn.closest === "function" ? btn.closest(".pg-context-bar") : null;
    let hunkEl = btn && typeof btn.closest === "function" ? btn.closest(".code-hunk") : null;
    if (barEl && hunkEl === barEl) {
      hunkEl = null;
    }
    if (!barEl && typeof document !== "undefined") {
      barEl = document.querySelector(`.pg-context-bar[data-file="${safeEscapeCss(filePath)}"][data-from-line="${fromLine}"][data-to-line="${toLine}"]`) ||
              document.querySelector(`.pg-context-bar[data-file="${safeEscapeCss(filePath)}"]`);
    }
    if (btn && btn.tagName === "BUTTON") {
      btn.disabled = true;
      btn.textContent = "Expanding…";
    }

    let fileLines = null;
    const cacheKey = `${clNum}:${ps || 1}:${filePath}`;
    if (state.fileContentCache && state.fileContentCache[cacheKey]) {
      fileLines = state.fileContentCache[cacheKey];
    } else if (typeof fetch === "function" && typeof sidecarFetch === "function") {
      try {
        const resp = await sidecarFetch(`/api/gerrit/file-content?cl=${clNum}&file=${encodeURIComponent(filePath)}&ps=${ps || 1}`);
        if (resp && resp.ok) {
          const data = await resp.json();
          if (data && Array.isArray(data.lines)) {
            if (!state.fileContentCache) state.fileContentCache = {};
            state.fileContentCache[cacheKey] = data.lines;
            fileLines = data.lines;
          }
        }
      } catch (_e) {}
    }

    const hasKnownTo = typeof toLine === "number" && toLine > 0;
    const resolvedToLine = hasKnownTo ? toLine : (fileLines ? fileLines.length : fromLine - 1);
    if (resolvedToLine < fromLine) {
      if (barEl && typeof barEl.remove === "function") {
        barEl.remove();
      }
      if (typeof showToast === "function") {
        showToast(`Already at end of file for ${escapeHtml(filePath)}`);
      }
      return 0;
    }

    const prefs = getPgDiffPreferences();
    const isTrailing = barEl && barEl.classList && barEl.classList.contains("pg-trailing-context-bar");
    const chunkSize = Number(count) > 0 ? Number(count) : (prefs.context === "all" ? (resolvedToLine - fromLine + 1) : (Number(prefs.context) || 10));
    let expandFrom = fromLine;
    let expandTo = resolvedToLine;

    if (mode === "below" || (isTrailing && mode !== "all")) {
      expandFrom = fromLine;
      expandTo = Math.min(resolvedToLine, fromLine + chunkSize - 1);
    } else if (mode === "above") {
      expandTo = resolvedToLine;
      expandFrom = Math.max(fromLine, resolvedToLine - chunkSize + 1);
    } else {
      expandFrom = fromLine;
      expandTo = resolvedToLine;
    }
    const expandedCount = expandTo - expandFrom + 1;

    const slicedLines = [];
    for (let ln = expandFrom; ln <= expandTo; ln++) {
      let text = "";
      if (fileLines && (ln - 1) < fileLines.length) {
        text = fileLines[ln - 1];
      } else {
        text = `// Context line ${ln}`;
      }
      slicedLines.push({
        type: "ctx",
        old: ln,
        new: ln,
        text: " " + text,
        clNum: clNum,
      });
    }

    const scheme = state.diffColorScheme || "syntax_intraline";
    const langInfo = detectDiffFileLanguage(filePath);
    const fileComments = getInlineCommentsForFile(clNum, filePath);
    const isSbs = barEl && barEl.classList ? barEl.classList.contains("code-line-sbs") : false;
    const safeFile = escapeHtml(filePath);

    let rowsHtml = "";
    if (isSbs) {
      rowsHtml = slicedLines.map(line => {
        const lnStr = String(line.new);
        const codeHtml = renderHighlightedDiffLineHtml(line.text, "ctx", langInfo.id, scheme, null);
        const threadCls = getLineThreadIndicatorClass(fileComments, lnStr, lnStr, null, line.new);
        const inlineCommentsHtml = renderInlineLineCommentsHtml(clNum, filePath, lnStr, lnStr, fileComments, null, line.new);
        const leftSummary = getPgLineCommentSummary(fileComments, lnStr, "left", line.new, filePath);
        const rightSummary = getPgLineCommentSummary(fileComments, lnStr, "right", line.new, filePath);
        const leftBadge = leftSummary ? renderPgGutterCommentBadgeHtml(leftSummary, clNum, filePath, "left", lnStr) : "";
        const rightBadge = rightSummary ? renderPgGutterCommentBadgeHtml(rightSummary, clNum, filePath, "right", lnStr) : "";
        const leftHasComment = leftSummary ? "has-comment-badge" : "";
        const rightHasComment = rightSummary ? "has-comment-badge" : "";
        return `<div class="code-line code-line-sbs code-ctx pg-expanded-ctx-row ${threadCls}" data-cl="${clNum}" data-file="${safeFile}" data-old-ln="${escapeHtml(lnStr)}" data-new-ln="${escapeHtml(lnStr)}" data-type="ctx">
          <span class="ln ln-old ${leftHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-side="left" title="Line ${escapeHtml(lnStr)} (Base) · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent on Base line · Shift+Click for range"><span class="ln-add-btn" title="Add comment on Base line (Alt+Click to Ask Agent)">+</span>${leftBadge}${escapeHtml(lnStr)}</span>
          <span class="code-text code-side-cell code-side-left side-ctx">${codeHtml}</span>
          <span class="ln ln-new ${rightHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-side="right" title="Line ${escapeHtml(lnStr)} · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent · Shift+Click for range"><span class="ln-add-btn" title="Add comment on line (Alt+Click to Ask Agent)">+</span>${rightBadge}${escapeHtml(lnStr)}</span>
          <span class="code-text code-side-cell code-side-right side-ctx">${codeHtml}</span>
        </div>${inlineCommentsHtml}`;
      }).join("");
    } else {
      rowsHtml = slicedLines.map(line => {
        const lnStr = String(line.new);
        const codeHtml = renderHighlightedDiffLineHtml(line.text, "ctx", langInfo.id, scheme, null);
        const threadCls = getLineThreadIndicatorClass(fileComments, lnStr, lnStr, null, line.new);
        const inlineCommentsHtml = renderInlineLineCommentsHtml(clNum, filePath, lnStr, lnStr, fileComments, null, line.new);
        const leftSummary = getPgLineCommentSummary(fileComments, lnStr, "left", line.new, filePath);
        const rightSummary = getPgLineCommentSummary(fileComments, lnStr, "right", line.new, filePath);
        const leftBadge = leftSummary ? renderPgGutterCommentBadgeHtml(leftSummary, clNum, filePath, "left", lnStr) : "";
        const rightBadge = rightSummary ? renderPgGutterCommentBadgeHtml(rightSummary, clNum, filePath, "right", lnStr) : "";
        const leftHasComment = leftSummary ? "has-comment-badge" : "";
        const rightHasComment = rightSummary ? "has-comment-badge" : "";
        return `<div class="code-line code-ctx pg-expanded-ctx-row ${threadCls}" data-cl="${clNum}" data-file="${safeFile}" data-old-ln="${escapeHtml(lnStr)}" data-new-ln="${escapeHtml(lnStr)}" data-type="ctx">
          <span class="ln ln-old ${leftHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-side="left" title="Line ${escapeHtml(lnStr)} (Base) · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent on Base line · Shift+Click for range"><span class="ln-add-btn" title="Add comment on Base line (Alt+Click to Ask Agent)">+</span>${leftBadge}${escapeHtml(lnStr)}</span>
          <span class="ln ln-new ${rightHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-side="right" title="Line ${escapeHtml(lnStr)} · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent · Shift+Click for range"><span class="ln-add-btn" title="Add comment on line (Alt+Click to Ask Agent)">+</span>${rightBadge}${escapeHtml(lnStr)}</span>
          <span class="code-text">${codeHtml}</span>
        </div>${inlineCommentsHtml}`;
      }).join("");
    }

    let newBarEl = null;
    if (barEl) {
      if (mode === "below" || isTrailing) {
        if (mode === "all") {
          if (typeof barEl.insertAdjacentHTML === "function") {
            barEl.insertAdjacentHTML("beforebegin", rowsHtml);
          }
          if (typeof barEl.remove === "function") {
            barEl.remove();
          }
        } else {
          if (typeof barEl.insertAdjacentHTML === "function") {
            barEl.insertAdjacentHTML("beforebegin", rowsHtml);
          }
          const remainingFrom = expandTo + 1;
          const remainingTo = resolvedToLine;
          if (remainingFrom <= remainingTo) {
            const renderTrailingFn = (typeof window !== "undefined" && typeof window.renderPgTrailingContextControlBarHtml === "function")
              ? window.renderPgTrailingContextControlBarHtml
              : renderPgTrailingContextControlBarHtml;
            const renderCtxFn = (typeof window !== "undefined" && typeof window.renderPgContextControlBarHtml === "function")
              ? window.renderPgContextControlBarHtml
              : renderPgContextControlBarHtml;
            const newBarHtml = isTrailing
              ? renderTrailingFn(clNum, safeFile, ps, remainingFrom, remainingTo, isSbs)
              : renderCtxFn(clNum, safeFile, ps, remainingFrom, remainingTo, isSbs);
            if (typeof barEl.outerHTML !== "undefined") {
              barEl.outerHTML = newBarHtml;
            }
            if (typeof document !== "undefined" && typeof document.querySelector === "function") {
              newBarEl = document.querySelector(`.pg-context-bar[data-file="${safeEscapeCss(filePath)}"][data-from-line="${remainingFrom}"][data-to-line="${remainingTo}"]`);
            }
          } else if (typeof barEl.remove === "function") {
            barEl.remove();
          }
        }
      } else if (mode === "above") {
        if (typeof barEl.insertAdjacentHTML === "function") {
          barEl.insertAdjacentHTML("afterend", rowsHtml);
        }
        const remainingFrom = fromLine;
        const remainingTo = expandFrom - 1;
        if (remainingFrom <= remainingTo) {
          const newBarHtml = renderPgContextControlBarHtml(clNum, safeFile, ps, remainingFrom, remainingTo, isSbs);
          if (typeof barEl.outerHTML !== "undefined") {
            barEl.outerHTML = newBarHtml;
          }
          if (typeof document !== "undefined" && typeof document.querySelector === "function") {
            newBarEl = document.querySelector(`.pg-context-bar[data-file="${safeEscapeCss(filePath)}"][data-from-line="${remainingFrom}"][data-to-line="${remainingTo}"]`);
          }
        } else if (typeof barEl.remove === "function") {
          barEl.remove();
        }
      } else {
        // "all"
        if (typeof barEl.insertAdjacentHTML === "function") {
          barEl.insertAdjacentHTML("beforebegin", rowsHtml);
        }
        if (typeof barEl.remove === "function") {
          barEl.remove();
        }
      }
    } else if (hunkEl) {
      if (typeof hunkEl.insertAdjacentHTML === "function") {
        hunkEl.insertAdjacentHTML("beforebegin", rowsHtml);
      }
    }

    if (typeof document !== "undefined") {
      const remainingFrom = mode === "above" ? fromLine : (expandTo + 1);
      const remainingTo = mode === "above" ? (expandFrom - 1) : toLine;
      const targetHunkEl = (hunkEl && hunkEl !== barEl) ? hunkEl : (typeof document.querySelector === "function" ? document.querySelector(`.code-hunk[data-file="${safeEscapeCss(filePath)}"][data-to-line="${toLine}"]`) : null);
      if (targetHunkEl && typeof targetHunkEl.setAttribute === "function") {
        const nextFrom = (mode === "all" || remainingFrom > remainingTo) ? 0 : remainingFrom;
        const nextTo = (mode === "all" || remainingFrom > remainingTo) ? 0 : remainingTo;
        targetHunkEl.setAttribute("data-from-line", String(nextFrom));
        targetHunkEl.setAttribute("data-to-line", String(nextTo));
        const actionsEl = typeof targetHunkEl.querySelector === "function" ? targetHunkEl.querySelector(".hunk-expand-actions") : null;
        if (actionsEl) {
          const badgeEl = typeof actionsEl.querySelector === "function" ? actionsEl.querySelector(".hunk-line-count-badge") : null;
          const badgeText = badgeEl ? badgeEl.textContent : "";
          const renderHunkActionsFn = (typeof window !== "undefined" && typeof window.renderPgHunkExpandActionsHtml === "function")
            ? window.renderPgHunkExpandActionsHtml
            : renderPgHunkExpandActionsHtml;
          actionsEl.outerHTML = renderHunkActionsFn(clNum, filePath, ps, nextFrom, nextTo, badgeText, "");
        }
      }
    }

    if (newBarEl && typeof newBarEl.focus === "function") {
      try {
        newBarEl.focus({ preventScroll: true });
      } catch (_) {
        newBarEl.focus();
      }
    }

    const remainingCount = (mode === "below") ? (toLine - expandTo) : (mode === "above" ? (expandFrom - fromLine) : 0);
    if (typeof showToast === "function") {
      if (remainingCount > 0) {
        const modeDesc = mode === "below" ? "below" : "above";
        showToast(`Expanded ${expandedCount} context lines ${modeDesc} in ${escapeHtml(filePath)} (${remainingCount} lines remaining · Shift+X for all)`);
      } else {
        showToast(`Expanded all ${expandedCount} context lines in ${escapeHtml(filePath)} ✓`);
      }
    }
    return expandedCount;
  }

  async function expandAllVisibleDiffContext() {
    if (typeof document === "undefined") return;
    const bars = Array.from(document.querySelectorAll(".pg-context-bar"));
    if (bars.length === 0) {
      if (typeof showToast === "function") showToast("All diff context lines are already expanded");
      return;
    }
    if (typeof showToast === "function") showToast(`Expanding context across ${bars.length} hunk gaps…`);
    for (const bar of bars) {
      const cl = Number(bar.getAttribute("data-cl") || state.selectedClNumber || 0);
      const file = bar.getAttribute("data-file") || "";
      const ps = Number(bar.getAttribute("data-ps") || 1);
      const fromLine = Number((typeof bar.getAttribute === "function" ? bar.getAttribute("data-from-line") : null) || 1);
      const toLine = Number((typeof bar.getAttribute === "function" ? bar.getAttribute("data-to-line") : null) || 1);
      const allBtn = (typeof bar.querySelector === "function" ? bar.querySelector(".pg-btn-expand-all") : null) || bar;
      await expandPgDiffContext({ preventDefault() {}, stopPropagation() {}, currentTarget: allBtn }, cl, file, ps, fromLine, toLine, "all");
    }
    if (typeof showToast === "function") showToast("All diff context lines expanded ✓");
  }

  function isPgFileContextExpanded(clNum, filePath, ps = null) {
    if (!state.expandedContextFiles || state.expandedContextFiles.size === 0) return false;
    const num = Number(clNum || state.selectedClNumber || 0);
    const p = Number(ps || (state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    const key = `${num}:${p}:${filePath}`;
    return state.expandedContextFiles.has(key);
  }

  async function expandPgFileDiffContext(clNum, filePath, ps = null) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const p = Number(ps || (state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    if (typeof document === "undefined") return 0;
    const safeFileCss = typeof safeEscapeCss === "function" ? safeEscapeCss(filePath) : filePath;
    const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${safeFileCss}"]`);
    if (!fileBlock) {
      if (typeof showToast === "function") showToast(`File diff for ${escapeHtml(filePath)} not found`);
      return 0;
    }
    const bars = Array.from(fileBlock.querySelectorAll(".pg-context-bar"));
    if (bars.length === 0) {
      if (typeof showToast === "function") showToast(`All context lines already expanded for ${escapeHtml(filePath)}`);
      return 0;
    }
    if (typeof showToast === "function") showToast(`Expanding ${bars.length} context gap${bars.length > 1 ? "s" : ""} in ${escapeHtml(filePath)}…`);
    let totalExpanded = 0;
    for (const bar of bars) {
      const fromLine = Number((typeof bar.getAttribute === "function" ? bar.getAttribute("data-from-line") : null) || 1);
      const toLine = Number((typeof bar.getAttribute === "function" ? bar.getAttribute("data-to-line") : null) || 1);
      const allBtn = (typeof bar.querySelector === "function" ? bar.querySelector(".pg-btn-expand-all") : null) || bar;
      const count = await expandPgDiffContext({ preventDefault() {}, stopPropagation() {}, currentTarget: allBtn }, num, filePath, p, fromLine, toLine, "all");
      totalExpanded += (Number(count) || 0);
    }
    if (!state.expandedContextFiles) state.expandedContextFiles = new Set();
    state.expandedContextFiles.add(`${num}:${p}:${filePath}`);

    const hdrBtn = fileBlock.querySelector(".pg-btn-file-context");
    if (hdrBtn) {
      hdrBtn.classList.add("is-expanded");
      hdrBtn.title = "Restore diff hunks (Shortcut: Shift+Alt+X)";
      const txt = hdrBtn.querySelector(".pg-file-context-text");
      if (txt) txt.textContent = "Diff hunks only";
    }
    const ftrBtn = fileBlock.querySelector(".pg-btn-file-footer-context");
    if (ftrBtn) {
      ftrBtn.classList.add("is-expanded");
      ftrBtn.title = "Restore diff hunks (Shortcut: Shift+Alt+X)";
      ftrBtn.textContent = "Diff hunks only";
    }

    if (typeof showToast === "function") showToast(`All context lines expanded for ${escapeHtml(filePath)} (${totalExpanded} lines) ✓`);
    return totalExpanded;
  }

  function restorePgFileDiffHunks(clNum, filePath, ps = null) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const p = Number(ps || (state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    const key = `${num}:${p}:${filePath}`;
    if (state.expandedContextFiles) {
      state.expandedContextFiles.delete(key);
    }
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    if (!fileObj || typeof document === "undefined") {
      if (typeof showToast === "function") showToast(`Diff data unavailable to restore hunks for ${escapeHtml(filePath)}`);
      return false;
    }
    const safeFileCss = typeof safeEscapeCss === "function" ? safeEscapeCss(filePath) : filePath;
    const fileBlock = document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${safeFileCss}"]`);
    if (fileBlock && typeof fileBlock.outerHTML !== "undefined") {
      const isVisible = fileBlock.style.display !== "none";
      const newHtml = renderFileDiffBlock(fileObj, num, p, isVisible);
      fileBlock.outerHTML = newHtml;
      if (typeof showToast === "function") showToast(`Restored original diff hunks for ${escapeHtml(filePath)}`);
      return true;
    }
    return false;
  }

  async function togglePgFileContext(event, clNum, filePath, ps = null) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const p = Number(ps || (state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    if (isPgFileContextExpanded(num, filePath, p)) {
      restorePgFileDiffHunks(num, filePath, p);
    } else {
      await expandPgFileDiffContext(num, filePath, p);
    }
  }

  /* PolyGerrit Diff Blame Layer (<gr-diff-blame>) Helpers & Lifecycle */
  function isPgFileBlameActive(clNum, filePath) {
    if (!state.activeBlameFiles || state.activeBlameFiles.size === 0) return false;
    const num = Number(clNum || state.selectedClNumber || 0);
    const safeFile = String(filePath || "").trim();
    return state.activeBlameFiles.has(`${num}:${safeFile}`);
  }

  function setPgFileBlameActive(clNum, filePath, active) {
    if (!state.activeBlameFiles) state.activeBlameFiles = new Set();
    const num = Number(clNum || state.selectedClNumber || 0);
    const safeFile = String(filePath || "").trim();
    const key = `${num}:${safeFile}`;
    if (active) {
      state.activeBlameFiles.add(key);
    } else {
      state.activeBlameFiles.delete(key);
    }
  }

  function buildPgLineBlameMap(blameList) {
    const byLine = {};
    const byCommit = {};
    if (!Array.isArray(blameList)) return { byLine, byCommit };

    for (let bIdx = 0; bIdx < blameList.length; bIdx++) {
      const block = blameList[bIdx];
      if (!block) continue;
      const sha = String(block.id || "").trim();
      const shortSha = sha ? sha.slice(0, 8) : `c${bIdx + 1}`;
      const authorRaw = String(block.author || "").trim();
      let authorName = authorRaw;
      const emailMatch = authorRaw.match(/^([^<]+)<([^>]+)>/);
      if (emailMatch) {
        authorName = emailMatch[1].trim();
      }
      const shortAuthor = authorName.split(" ")[0] || authorName;
      const commitMsg = String(block.commit_msg || "").trim();
      const subject = commitMsg.split("\n")[0] || "";
      let dateStr = "";
      if (block.time) {
        const t = typeof block.time === "number" ? (block.time * (block.time < 1e11 ? 1000 : 1)) : Date.parse(block.time);
        if (!isNaN(t)) {
          const d = new Date(t);
          dateStr = d.toISOString().slice(0, 10);
        }
      }
      const fullTitle = `[${shortSha}] ${subject}\nAuthor: ${authorName}\nDate: ${dateStr}`;
      let colorHash = 0;
      for (let c = 0; c < sha.length; c++) {
        colorHash = (colorHash * 31 + sha.charCodeAt(c)) & 0xffffffff;
      }
      const colorIdx = Math.abs(colorHash) % 8;

      const commitInfo = {
        block,
        sha,
        shortSha,
        authorName,
        shortAuthor,
        subject,
        dateStr,
        fullTitle,
        colorIdx,
      };
      byCommit[shortSha] = commitInfo;

      const ranges = Array.isArray(block.ranges) ? block.ranges : [];
      for (const range of ranges) {
        const start = Number(range.start || 0);
        const end = Number(range.end || 0);
        if (start > 0 && end >= start) {
          for (let l = start; l <= end; l++) {
            byLine[l] = {
              ...commitInfo,
              isFirstInRange: l === start,
            };
          }
        }
      }
    }
    return { byLine, byCommit };
  }

  function renderPgBlameCellHtml(entry, lineNum, isHunk = false) {
    if (isHunk) {
      return `<span class="pg-blame-col pg-blame-hunk-col"></span>`;
    }
    if (!entry) {
      return `<span class="pg-blame-col"></span>`;
    }
    const colorCls = `pg-blame-c${entry.colorIdx % 8}`;
    const firstCls = entry.isFirstInRange ? "is-first-in-range" : "is-continuation";
    const titleAttr = escapeHtml(entry.fullTitle);
    const safeSha = escapeHtml(entry.shortSha);
    const safeAuthor = escapeHtml(entry.shortAuthor);
    const safeDate = escapeHtml(entry.dateStr);
    const content = entry.isFirstInRange
      ? `<span class="pg-blame-sha" title="${titleAttr}">${safeSha}</span><span class="pg-blame-author" title="${escapeHtml(entry.authorName)}">${safeAuthor}</span><span class="pg-blame-date">${safeDate}</span>`
      : `<span class="pg-blame-continuation">·</span>`;
    return `<span class="pg-blame-col ${colorCls} ${firstCls}" data-blame-id="${safeSha}" title="${titleAttr}">${content}</span>`;
  }

  function applyPgBlameToDomTable(tableEl, blameList, clNum, filePath) {
    if (!tableEl || typeof tableEl.querySelectorAll !== "function") return;
    const blameMap = buildPgLineBlameMap(blameList);
    const rows = tableEl.querySelectorAll(".code-line");
    rows.forEach(row => {
      let blameCol = (typeof row.querySelector === "function") ? row.querySelector(".pg-blame-col") : null;
      if (!blameCol && typeof document !== "undefined" && typeof document.createElement === "function") {
        blameCol = document.createElement("span");
        blameCol.className = "pg-blame-col";
        row.insertBefore(blameCol, row.firstChild);
      }
      if (!blameCol) return;
      if (row.classList.contains("code-hunk")) {
        blameCol.className = "pg-blame-col pg-blame-hunk-col";
        blameCol.innerHTML = "";
        return;
      }
      const oldLn = row.getAttribute("data-old-ln");
      const newLn = row.getAttribute("data-new-ln");
      const lineNum = oldLn ? parseInt(oldLn, 10) : (newLn ? parseInt(newLn, 10) : 0);
      const entry = lineNum ? blameMap.byLine[lineNum] : null;
      if (entry) {
        const colorCls = `pg-blame-c${entry.colorIdx % 8}`;
        const firstCls = entry.isFirstInRange ? "is-first-in-range" : "is-continuation";
        blameCol.className = `pg-blame-col ${colorCls} ${firstCls}`;
        blameCol.setAttribute("data-blame-id", entry.shortSha);
        blameCol.setAttribute("title", entry.fullTitle);
        blameCol.onmouseenter = (e) => onPgBlameCellMouseEnter(e, entry.shortSha, tableEl);
        blameCol.onmouseleave = (e) => onPgBlameCellMouseLeave(e, entry.shortSha, tableEl);
        if (entry.isFirstInRange) {
          const safeSha = escapeHtml(entry.shortSha);
          const safeAuthor = escapeHtml(entry.shortAuthor);
          const safeDate = escapeHtml(entry.dateStr);
          const safeTitle = escapeHtml(entry.fullTitle);
          blameCol.innerHTML = `<span class="pg-blame-sha" title="${safeTitle}">${safeSha}</span><span class="pg-blame-author" title="${escapeHtml(entry.authorName)}">${safeAuthor}</span><span class="pg-blame-date">${safeDate}</span>`;
        } else {
          blameCol.innerHTML = `<span class="pg-blame-continuation">·</span>`;
        }
        row.setAttribute("data-blame-id", entry.shortSha);
      } else {
        blameCol.className = "pg-blame-col";
        blameCol.innerHTML = "";
      }
    });
  }

  function onPgBlameCellMouseEnter(e, shortSha, tableEl) {
    if (!shortSha || !tableEl || typeof tableEl.querySelectorAll !== "function") return;
    const safeSha = typeof CSS !== "undefined" && typeof CSS.escape === "function" ? CSS.escape(shortSha) : shortSha;
    const matchingRows = tableEl.querySelectorAll(`.code-line[data-blame-id="${safeSha}"]`);
    matchingRows.forEach(r => r.classList.add("pg-blame-hovered"));
  }

  function onPgBlameCellMouseLeave(e, shortSha, tableEl) {
    if (!shortSha || !tableEl || typeof tableEl.querySelectorAll !== "function") return;
    const safeSha = typeof CSS !== "undefined" && typeof CSS.escape === "function" ? CSS.escape(shortSha) : shortSha;
    const matchingRows = tableEl.querySelectorAll(`.code-line[data-blame-id="${safeSha}"]`);
    matchingRows.forEach(r => r.classList.remove("pg-blame-hovered"));
  }

  async function togglePgFileBlame(event, clNum, filePath, patchSet = null) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const safeFile = String(filePath || "").trim();
    if (!num || !safeFile) return;
    const ps = patchSet || (state.diffsByCl && state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1;
    const fileKey = `${num}:${safeFile}`;
    const cacheKey = `${num}:${ps}:${safeFile}`;

    if (!state.activeBlameFiles) state.activeBlameFiles = new Set();
    if (!state.fileBlameCache) state.fileBlameCache = {};

    const wasActive = state.activeBlameFiles.has(fileKey);
    const newActive = !wasActive;

    const safeId = safeDomId(safeFile);
    const fileBlock = (typeof document !== "undefined")
      ? (document.getElementById(`pgFileBlock-${num}-${safeId}`) ||
         document.getElementById(`fileBlock-${num}-${safeId}`) ||
         (typeof document.querySelector === "function" ? document.querySelector(`.pg-file-diff-block[data-cl="${num}"][data-file="${escapeJsQuote(safeFile)}"]`) : null))
      : null;
    const tableEl = fileBlock && typeof fileBlock.querySelector === "function" ? fileBlock.querySelector(".code-table") : null;
    const headerBtn = fileBlock && typeof fileBlock.querySelector === "function" ? fileBlock.querySelector(".pg-btn-file-blame") : null;
    const footerBtn = fileBlock && typeof fileBlock.querySelector === "function" ? fileBlock.querySelector(".pg-btn-file-footer-blame") : null;

    if (newActive) {
      state.activeBlameFiles.add(fileKey);
      if (headerBtn) {
        headerBtn.classList.add("is-active");
        headerBtn.title = "Hide blame annotations (Shortcut: b)";
        const txt = headerBtn.querySelector(".pg-file-blame-text");
        if (txt) txt.textContent = "Loading blame…";
      }
      if (footerBtn) {
        footerBtn.classList.add("is-active");
        footerBtn.title = "Hide blame annotations (Shortcut: b)";
        footerBtn.innerHTML = '<span aria-hidden="true">📜</span> Hide blame';
      }

      let blameList = state.fileBlameCache[cacheKey];
      if (!blameList) {
        try {
          const url = `/api/gerrit/blame?cl=${num}&file=${encodeURIComponent(safeFile)}&ps=${ps}`;
          const resp = typeof sidecarFetch === "function" ? await sidecarFetch(url) : (typeof fetch === "function" ? await fetch(url) : null);
          if (resp && resp.ok) {
            const data = await resp.json();
            blameList = Array.isArray(data.blame) ? data.blame : [];
          } else {
            blameList = [];
          }
        } catch (err) {
          console.warn("Failed to fetch blame:", err);
          blameList = [];
        }
        state.fileBlameCache[cacheKey] = blameList;
      }
      if (headerBtn) {
        const txt = headerBtn.querySelector(".pg-file-blame-text");
        if (txt) txt.textContent = "Hide blame";
      }
      if (tableEl) {
        tableEl.classList.add("has-blame");
        applyPgBlameToDomTable(tableEl, blameList, num, safeFile);
      }
      const commitsCount = Array.isArray(blameList) ? blameList.length : 0;
      if (typeof showToast === "function") {
        showToast(`Blame enabled for ${safeFile} (${commitsCount} commits) ✓`);
      }
    } else {
      state.activeBlameFiles.delete(fileKey);
      if (headerBtn) {
        headerBtn.classList.remove("is-active");
        headerBtn.title = "Show blame annotations (Shortcut: b)";
        const txt = headerBtn.querySelector(".pg-file-blame-text");
        if (txt) txt.textContent = "Blame";
      }
      if (footerBtn) {
        footerBtn.classList.remove("is-active");
        footerBtn.title = "Show blame annotations (Shortcut: b)";
        footerBtn.innerHTML = '<span aria-hidden="true">📜</span> Blame';
      }
      if (tableEl) {
        tableEl.classList.remove("has-blame");
      }
      if (typeof showToast === "function") {
        showToast(`Blame disabled for ${safeFile}`);
      }
    }
  }

