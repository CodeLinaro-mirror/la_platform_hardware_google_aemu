  const _DIFF_HUNK_NEW_START_RE = /@@\s*-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s*@@/;
  const _DIFF_LEFT_ADD_BTN_HTML = '<span class="ln-add-btn" title="Add comment on Base line (Alt+Click to Ask Agent)">+</span>';
  const _DIFF_RIGHT_ADD_BTN_HTML = '<span class="ln-add-btn" title="Add comment on line (Alt+Click to Ask Agent)">+</span>';
  const _DIFF_LEFT_TITLE_SUFFIX = " (Base) · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent on Base line · Shift+Click for range";
  const _DIFF_RIGHT_TITLE_SUFFIX = " · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent · Shift+Click for range";
  const _DEFAULT_EMPTY_BLAME_CELL_HTML = '<span class="pg-blame-col"></span>';
  const _UNI_LEFT_GUTTER_OPEN = '<span class="ln ln-old " onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="';
  const _UNI_LEFT_GUTTER_MID = '" data-side="left" title="Line ';
  const _UNI_LEFT_GUTTER_BTN = `${_DIFF_LEFT_TITLE_SUFFIX}">${_DIFF_LEFT_ADD_BTN_HTML}`;
  const _UNI_LEFT_EMPTY_OPEN = '<span class="ln ln-old " onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="';
  const _UNI_LEFT_EMPTY_CLOSE = '" data-side="left" title="Base line empty"></span>';
  const _UNI_RIGHT_GUTTER_OPEN = '<span class="ln ln-new " onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="';
  const _UNI_RIGHT_GUTTER_MID = '" data-side="right" title="Line ';
  const _UNI_RIGHT_GUTTER_BTN = `${_DIFF_RIGHT_TITLE_SUFFIX}">${_DIFF_RIGHT_ADD_BTN_HTML}`;
  const _UNI_RIGHT_EMPTY_OPEN = '<span class="ln ln-new " onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="';
  const _UNI_RIGHT_EMPTY_CLOSE = '" data-side="right" title="Revision line empty"></span>';
  const _BLANK_OLD_GUTTER_HTML = '<span class="ln ln-old ln-blank"></span>';
  const _BLANK_NEW_GUTTER_HTML = '<span class="ln ln-new ln-blank"></span>';
  const _SBS_LEFT_GUTTER_OPEN = '<span class="ln ln-old " onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="';
  const _SBS_LEFT_GUTTER_MID = ' data-side="left" title="Line ';
  const _SBS_LEFT_GUTTER_MID_QUOTED = '" data-side="left" title="Line ';
  const _SBS_LEFT_GUTTER_BTN = " (Base) · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent on Base line · Shift+Click for range\"><span class=\"ln-add-btn\" title=\"Add comment on Base line (Alt+Click to Ask Agent)\">+</span>";
  const _SBS_RIGHT_GUTTER_OPEN = '<span class="ln ln-new " onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="';
  const _SBS_RIGHT_GUTTER_MID = ' data-side="right" title="Line ';
  const _SBS_RIGHT_GUTTER_MID_QUOTED = '" data-side="right" title="Line ';
  const _SBS_RIGHT_GUTTER_BTN = " · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent · Shift+Click for range\"><span class=\"ln-add-btn\" title=\"Add comment on line (Alt+Click to Ask Agent)\">+</span>";
  const _SBS_CTX_LEFT_CODE_OPEN = '</span><span class="code-text code-side-cell code-side-left side-ctx">';
  const _SBS_CTX_RIGHT_GUTTER_OPEN = '</span>' + _SBS_RIGHT_GUTTER_OPEN;
  const _SBS_CTX_RIGHT_CODE_OPEN = '</span><span class="code-text code-side-cell code-side-right side-ctx">';
  const _SBS_DEL_LEFT_CODE_OPEN = '<span class="code-text code-side-cell code-side-left side-del">';
  const _SBS_BLANK_LEFT_CODE_HTML = '<span class="code-text code-side-cell code-side-left side-blank"></span>';
  const _SBS_ADD_RIGHT_CODE_OPEN = '<span class="code-text code-side-cell code-side-right side-add">';
  const _SBS_BLANK_RIGHT_CODE_HTML = '<span class="code-text code-side-cell code-side-right side-blank"></span></div>';

  function buildFastUnifiedRowTail(ln, idx, lnType, oldVal, newVal, safeOldStr, safeNewStr, emptyBlameCellHtml, codeInnerHtml) {
    if (
      ln._uniRowTail !== undefined &&
      ln._uniRowIdx === idx &&
      ln._uniRowType === lnType &&
      ln._uniRowOld === oldVal &&
      ln._uniRowNew === newVal &&
      ln._uniRowCode === codeInnerHtml &&
      emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML
    ) {
      return ln._uniRowTail;
    }
    const leftGutterHtml = safeOldStr !== ""
      ? (_UNI_LEFT_GUTTER_OPEN + idx + _UNI_LEFT_GUTTER_MID + safeOldStr + _UNI_LEFT_GUTTER_BTN + safeOldStr + "</span>")
      : (_UNI_LEFT_EMPTY_OPEN + idx + _UNI_LEFT_EMPTY_CLOSE);
    const rightGutterHtml = safeNewStr !== ""
      ? (_UNI_RIGHT_GUTTER_OPEN + idx + _UNI_RIGHT_GUTTER_MID + safeNewStr + _UNI_RIGHT_GUTTER_BTN + safeNewStr + "</span>")
      : (_UNI_RIGHT_EMPTY_OPEN + idx + _UNI_RIGHT_EMPTY_CLOSE);
    const tail = idx + '" data-old-ln="' + safeOldStr + '" data-new-ln="' + safeNewStr + '" data-type="' + lnType + '">' +
      emptyBlameCellHtml + leftGutterHtml + rightGutterHtml + '<span class="code-text">' + codeInnerHtml + "</span></div>";
    if (emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML) {
      ln._uniRowIdx = idx;
      ln._uniRowType = lnType;
      ln._uniRowOld = oldVal;
      ln._uniRowNew = newVal;
      ln._uniRowCode = codeInnerHtml;
      ln._uniRowTail = tail;
    }
    return tail;
  }

  function buildFastSbsCtxRowTail(ln, idx, oldVal, newVal, safeOldStr, safeNewStr, emptyBlameCellHtml, codeHtml) {
    if (
      ln._sbsCtxTail !== undefined &&
      ln._sbsCtxIdx === idx &&
      ln._sbsCtxOld === oldVal &&
      ln._sbsCtxNew === newVal &&
      ln._sbsCtxCode === codeHtml &&
      emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML
    ) {
      return ln._sbsCtxTail;
    }
    const tail = idx + '" data-left-idx="' + idx + '" data-right-idx="' + idx + '" data-old-ln="' + safeOldStr + '" data-new-ln="' + safeNewStr + '" data-type="ctx">' +
      emptyBlameCellHtml +
      _SBS_LEFT_GUTTER_OPEN + idx + _SBS_LEFT_GUTTER_MID_QUOTED + safeOldStr + _SBS_LEFT_GUTTER_BTN + safeOldStr +
      _SBS_CTX_LEFT_CODE_OPEN + codeHtml +
      _SBS_CTX_RIGHT_GUTTER_OPEN + idx + _SBS_RIGHT_GUTTER_MID_QUOTED + safeNewStr + _SBS_RIGHT_GUTTER_BTN + safeNewStr +
      _SBS_CTX_RIGHT_CODE_OPEN + codeHtml + "</span></div>";
    if (emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML) {
      ln._sbsCtxIdx = idx;
      ln._sbsCtxOld = oldVal;
      ln._sbsCtxNew = newVal;
      ln._sbsCtxCode = codeHtml;
      ln._sbsCtxTail = tail;
    }
    return tail;
  }

  function buildFastSbsDiffRowTail(anchorLn, primaryIdx, lLine, lIdx, rLine, rIdx, oldVal, newVal, safeOldStr, safeNewStr, emptyBlameCellHtml, codeLeftHtml, codeRightHtml) {
    if (
      anchorLn &&
      anchorLn._sbsDiffTail !== undefined &&
      anchorLn._sbsDiffLIdx === lIdx &&
      anchorLn._sbsDiffRIdx === rIdx &&
      anchorLn._sbsDiffOld === oldVal &&
      anchorLn._sbsDiffNew === newVal &&
      anchorLn._sbsDiffLCode === codeLeftHtml &&
      anchorLn._sbsDiffRCode === codeRightHtml &&
      emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML
    ) {
      return anchorLn._sbsDiffTail;
    }
    const leftGutterHtml = lLine
      ? _SBS_LEFT_GUTTER_OPEN + lIdx + _SBS_LEFT_GUTTER_MID_QUOTED + safeOldStr + _SBS_LEFT_GUTTER_BTN + safeOldStr + "</span>"
      : _BLANK_OLD_GUTTER_HTML;
    const rightGutterHtml = rLine
      ? _SBS_RIGHT_GUTTER_OPEN + rIdx + _SBS_RIGHT_GUTTER_MID_QUOTED + safeNewStr + _SBS_RIGHT_GUTTER_BTN + safeNewStr + "</span>"
      : _BLANK_NEW_GUTTER_HTML;
    const tail = primaryIdx + '" data-left-idx="' + (lIdx != null ? lIdx : "") + '" data-right-idx="' + (rIdx != null ? rIdx : "") + '" data-old-ln="' + safeOldStr + '" data-new-ln="' + safeNewStr + '" data-type="diff">' +
      emptyBlameCellHtml +
      leftGutterHtml +
      (lLine ? _SBS_DEL_LEFT_CODE_OPEN + codeLeftHtml + "</span>" : _SBS_BLANK_LEFT_CODE_HTML) +
      rightGutterHtml +
      (rLine ? _SBS_ADD_RIGHT_CODE_OPEN + codeRightHtml + "</span></div>" : _SBS_BLANK_RIGHT_CODE_HTML);
    if (anchorLn && emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML) {
      anchorLn._sbsDiffLIdx = lIdx;
      anchorLn._sbsDiffRIdx = rIdx;
      anchorLn._sbsDiffOld = oldVal;
      anchorLn._sbsDiffNew = newVal;
      anchorLn._sbsDiffLCode = codeLeftHtml;
      anchorLn._sbsDiffRCode = codeRightHtml;
      anchorLn._sbsDiffTail = tail;
    }
    return tail;
  }

  function renderFileDiffUnifiedHtml(
    f,
    fallbackCl,
    _ps,
    safeFile,
    fileComments,
    scheme,
    langInfo,
    intralineByIdx,
    _maxDigits
  ) {
    const rawLines = Array.isArray(f.lines) ? f.lines : [];
    if (rawLines.length === 0) return "";
    const ps = _ps || 1;
    const fileBlameList = hasAnyOwnProp(state.fileBlameCache)
      ? (state.fileBlameCache[`${fallbackCl}:${ps}:${safeFile}`] || null)
      : null;
    const blameMap = fileBlameList ? buildPgLineBlameMap(fileBlameList) : null;
    const blameHunkHtml = renderPgBlameCellHtml(null, 0, true);
    const emptyBlameCellHtml = renderPgBlameCellHtml(null, 0, false);
    const prefs = getPgDiffPreferences();
    const checkWsIgnore = Boolean(prefs && prefs.ignoreWhitespace !== "none");
    const showTabs = Boolean(prefs && prefs.showTabs);
    const showWsErrors = !prefs || prefs.showWhitespaceErrors !== false;
    const hasFileComments = Array.isArray(fileComments) && fileComments.length > 0;
    const commentLineIdx = hasFileComments ? buildPgFileCommentsLineIndex(fileComments) : null;
    const jsSafeFile = escapeJsQuote(safeFile);
    const baseClNum = Number(fallbackCl || 0);
    const hlSchemeKey = (scheme === "intraline_focus" || scheme === "semantic_review") ? scheme : "syntax_intraline";
    const hlCachePrefix = (scheme !== "classic" && (!prefs || prefs.syntaxHighlighting !== false))
      ? `${langInfo.id}\x00${hlSchemeKey}\x00${showTabs ? 1 : 0}${showWsErrors ? 1 : 0}1\x00`
      : "";
    const isCanonHlPrefix = hlCachePrefix === _CANONICAL_CPP_HL_PREFIX;
    const fastUniMid = `  " data-cl="${baseClNum}" data-file="${safeFile}" data-idx="`;
    const fastUniPrefixCtx = '<div class="code-line code-ctx' + fastUniMid;
    const fastUniPrefixAdd = '<div class="code-line code-add' + fastUniMid;
    const fastUniPrefixDel = '<div class="code-line code-del' + fastUniMid;
    const canFastUniRow = !hasFileComments && !blameMap && !checkWsIgnore;
    let canCacheWholeUniTails = canFastUniRow && rawLines.length >= 8 && emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML;
    const uniTailsStyleKey = canCacheWholeUniTails
      ? `${langInfo.id}\x00${scheme}\x00${showTabs ? 1 : 0}${showWsErrors ? 1 : 0}\x00${intralineByIdx && hasAnyOwnProp(intralineByIdx) ? 1 : 0}`
      : "";
    const firstUniTxt = canCacheWholeUniTails ? (rawLines[0].text || "") : "";
    const midUniTxt = canCacheWholeUniTails ? (rawLines[rawLines.length >> 1].text || "") : "";
    const lastUniTxt = canCacheWholeUniTails ? (rawLines[rawLines.length - 1].text || "") : "";
    let lastNewLn = 0;
    let html = "";
    let startIdx = 0;
    if (canCacheWholeUniTails) {
      let cachedUniObj = null;
      const fUniEntry = f._uniFastTailsEntry;
      if (
        fUniEntry &&
        fUniEntry.linesRef === rawLines &&
        fUniEntry.linesLen === rawLines.length &&
        fUniEntry.linesVer === f.lines_version &&
        fUniEntry.styleKey === uniTailsStyleKey &&
        fUniEntry.firstTxt === firstUniTxt &&
        fUniEntry.midTxt === midUniTxt &&
        fUniEntry.lastTxt === lastUniTxt
      ) {
        cachedUniObj = fUniEntry;
      } else if (f._cachedDerivedRef && f._cachedDerivedRef._uniTailsByStyle) {
        cachedUniObj = f._cachedDerivedRef._uniTailsByStyle.get(uniTailsStyleKey) || null;
        if (cachedUniObj) {
          f._uniFastTailsEntry = {
            linesRef: rawLines,
            linesLen: rawLines.length,
            linesVer: f.lines_version,
            styleKey: uniTailsStyleKey,
            firstTxt: firstUniTxt,
            midTxt: midUniTxt,
            lastTxt: lastUniTxt,
            tails: cachedUniObj.tails,
            types: cachedUniObj.types,
            lastNewLn: cachedUniObj.lastNewLn,
          };
        }
      }
      if (cachedUniObj) {
        const tails = cachedUniObj.tails;
        const types = cachedUniObj.types;
        for (let r = 0; r < tails.length; r++) {
          const tCode = types[r];
          html += (tCode === 0 ? fastUniPrefixCtx : (tCode === 1 ? fastUniPrefixAdd : fastUniPrefixDel)) + tails[r];
        }
        lastNewLn = cachedUniObj.lastNewLn;
        startIdx = rawLines.length;
      }
    }
    const builtUniTails = (canCacheWholeUniTails && startIdx === 0) ? [] : null;
    const builtUniTypes = (canCacheWholeUniTails && startIdx === 0) ? [] : null;

    for (let idx = startIdx; idx < rawLines.length; idx++) {
      const ln = rawLines[idx];
      const lnType = ln.type || "ctx";
      const cls = lnType === "add" ? "code-add" : (lnType === "del" ? "code-del" : (lnType === "hunk" ? "code-hunk" : "code-ctx"));
      if (lnType === "hunk") {
        canCacheWholeUniTails = false;
        const m = String(ln.text || "").match(_DIFF_HUNK_NEW_START_RE);
        const hunkNewStart = m ? parseInt(m[1], 10) : 0;
        let fromLine = 0;
        let toLine = 0;
        if (hunkNewStart > lastNewLn + 1) {
          fromLine = lastNewLn + 1;
          toLine = hunkNewStart - 1;
          html += renderPgContextControlBarHtml(fallbackCl, safeFile, ps, fromLine, toLine, false);
        }
        if (hunkNewStart > 0 && lastNewLn < hunkNewStart - 1) {
          lastNewLn = hunkNewStart - 1;
        }
        const actionsHtml = renderPgHunkExpandActionsHtml(fallbackCl, safeFile, ps, fromLine, toLine, ln.old, ln.new);
        html += `<div class="code-line ${cls}" data-type="hunk" data-cl="${fallbackCl}" data-file="${safeFile}" data-ps="${ps}" data-from-line="${fromLine}" data-to-line="${toLine}" data-idx="${idx}" tabindex="0" role="region" aria-label="Diff hunk line ${escapeHtml(ln.new || ln.old || "")}" onkeydown="onPgHunkHeaderKeyDown(event, ${fallbackCl}, '${jsSafeFile}', ${ps})">${blameHunkHtml}<span class="ln">${escapeHtml(ln.old)}</span><span class="ln">${escapeHtml(ln.new)}</span><div class="hunk-header-bar">${renderHunkHeaderInnerHtml(ln.text, scheme)}${actionsHtml}</div></div>`;
        continue;
      }
      const intra = intralineByIdx[idx];
      const lnText = ln.text || "";
      let codeInnerHtml;
      if (!intra && hlCachePrefix && lnText.length <= 512) {
        if (ln._hlPrefix === hlCachePrefix && ln._hlText === lnText && ln._hlHtml !== undefined) {
          codeInnerHtml = ln._hlHtml;
        } else {
          const lineKey = isCanonHlPrefix ? lnText : hlCachePrefix + lnText;
          codeInnerHtml = _pgHighlightedLineHtmlCache.get(lineKey);
          if (codeInnerHtml === undefined) {
            codeInnerHtml = renderAndCacheSyntaxLineHtml(lnText, langInfo.id, scheme, showTabs, showWsErrors, lineKey);
          }
          ln._hlPrefix = hlCachePrefix;
          ln._hlText = lnText;
          ln._hlHtml = codeInnerHtml;
        }
      } else if (intra && ln._intraRef === intra && ln._hlPrefix === hlCachePrefix && ln._hlText === lnText && ln._intraHtml !== undefined) {
        codeInnerHtml = ln._intraHtml;
      } else {
        codeInnerHtml = renderHighlightedDiffLineHtml(lnText, lnType, langInfo.id, scheme, intra);
        if (intra) {
          ln._intraRef = intra;
          ln._hlPrefix = hlCachePrefix;
          ln._hlText = lnText;
          ln._intraHtml = codeInnerHtml;
        }
      }
      const oldVal = ln.old;
      const newVal = ln.new;
      if (canFastUniRow && !ln.clNum && (lnType === "ctx" || lnType === "add" || lnType === "del")) {
        const safeOldStr = oldVal != null ? (typeof oldVal === "number" ? oldVal : (oldVal === "" ? "" : escapeHtml(String(oldVal)))) : "";
        const safeNewStr = newVal != null ? (typeof newVal === "number" ? newVal : (newVal === "" ? "" : escapeHtml(String(newVal)))) : "";
        if (safeNewStr !== "") {
          const parsedNew = typeof newVal === "number" ? newVal : parseInt(safeNewStr, 10);
          if (parsedNew > lastNewLn) lastNewLn = parsedNew;
        }
        const rowTail = buildFastUnifiedRowTail(ln, idx, lnType, oldVal, newVal, safeOldStr, safeNewStr, emptyBlameCellHtml, codeInnerHtml);
        const rowPrefix = lnType === "ctx" ? fastUniPrefixCtx : (lnType === "add" ? fastUniPrefixAdd : fastUniPrefixDel);
        html += rowPrefix + rowTail;
        if (canCacheWholeUniTails && builtUniTails) {
          builtUniTails.push(rowTail);
          builtUniTypes.push(lnType === "ctx" ? 0 : (lnType === "add" ? 1 : 2));
        }
        continue;
      }
      canCacheWholeUniTails = false;
      const lineCl = ln.clNum ? Number(ln.clNum) : baseClNum;
      const oldStr = oldVal != null ? String(oldVal) : "";
      const newStr = newVal != null ? String(newVal) : "";
      if (newStr !== "") {
        const parsedNew = typeof newVal === "number" ? newVal : parseInt(newStr, 10);
        if (parsedNew > lastNewLn) {
          lastNewLn = parsedNew;
        }
      }
      let threadCls = "";
      let inlineCommentsHtml = "";
      let leftGutterBadge = "";
      let rightGutterBadge = "";
      let leftHasComment = "";
      let rightHasComment = "";
      if (hasFileComments) {
        const oldLnNum = oldStr !== "" ? (typeof oldVal === "number" ? oldVal : parseInt(oldStr, 10)) : 0;
        const newLnNum = newStr !== "" ? (typeof newVal === "number" ? newVal : parseInt(newStr, 10)) : 0;
        const leftMatch = commentLineIdx.hasIdxRange || (oldLnNum > 0 && commentLineIdx.leftLines.has(oldLnNum));
        const rightMatch = commentLineIdx.hasIdxRange || (newLnNum > 0 && commentLineIdx.rightLines.has(newLnNum));
        if (leftMatch || rightMatch) {
          threadCls = getLineThreadIndicatorClass(fileComments, oldStr, newStr, null, idx);
          inlineCommentsHtml = renderInlineLineCommentsHtml(lineCl, f.file, oldStr, newStr, fileComments, null, idx);
          const leftGutterSummary = (leftMatch && oldStr) ? getPgLineCommentSummary(fileComments, oldStr, "left", idx, f.file) : null;
          const rightGutterSummary = (rightMatch && newStr) ? getPgLineCommentSummary(fileComments, newStr, "right", idx, f.file) : null;
          leftGutterBadge = leftGutterSummary ? renderPgGutterCommentBadgeHtml(leftGutterSummary, lineCl, f.file, "left", oldStr) : "";
          rightGutterBadge = rightGutterSummary ? renderPgGutterCommentBadgeHtml(rightGutterSummary, lineCl, f.file, "right", newStr) : "";
          leftHasComment = leftGutterSummary ? "has-comment-badge" : "";
          rightHasComment = rightGutterSummary ? "has-comment-badge" : "";
        }
      }
      const wsCls = (checkWsIgnore && (lnType === "add" || lnType === "del") && isWhitespaceDiffOnlyLine(rawLines, idx))
        ? "pg-ws-ignored"
        : "";
      const leftGutterBtn = oldStr !== "" ? _DIFF_LEFT_ADD_BTN_HTML : "";
      const rightGutterBtn = newStr !== "" ? _DIFF_RIGHT_ADD_BTN_HTML : "";
      const safeOldStr = (typeof oldVal === "number" || oldStr === "") ? oldStr : escapeHtml(oldStr);
      const safeNewStr = (typeof newVal === "number" || newStr === "") ? newStr : escapeHtml(newStr);
      const leftTitle = oldStr !== "" ? "Line " + safeOldStr + _DIFF_LEFT_TITLE_SUFFIX : "Base line empty";
      const rightTitle = newStr !== "" ? "Line " + safeNewStr + _DIFF_RIGHT_TITLE_SUFFIX : "Revision line empty";
      let blameCellHtml = emptyBlameCellHtml;
      let blameAttr = "";
      if (blameMap) {
        const targetLineNum = oldStr ? (typeof oldVal === "number" ? oldVal : parseInt(oldStr, 10)) : (newStr ? (typeof newVal === "number" ? newVal : parseInt(newStr, 10)) : 0);
        const blameEntry = targetLineNum ? blameMap.byLine[targetLineNum] : null;
        if (blameEntry) {
          blameCellHtml = renderPgBlameCellHtml(blameEntry, targetLineNum, false);
          blameAttr = ` data-blame-id="${escapeHtml(blameEntry.shortSha)}"`;
        }
      }
      const safeLnType = (lnType === "ctx" || lnType === "add" || lnType === "del") ? lnType : escapeHtml(lnType);
      html += `<div class="code-line ${cls} ${threadCls} ${wsCls}" data-cl="${lineCl}" data-file="${safeFile}" data-idx="${idx}" data-old-ln="${safeOldStr}" data-new-ln="${safeNewStr}" data-type="${safeLnType}"${blameAttr}>${blameCellHtml}<span class="ln ln-old ${leftHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="${idx}" data-side="left" title="${leftTitle}">${leftGutterBtn}${leftGutterBadge}${safeOldStr}</span><span class="ln ln-new ${rightHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="${idx}" data-side="right" title="${rightTitle}">${rightGutterBtn}${rightGutterBadge}${safeNewStr}</span><span class="code-text">${codeInnerHtml}</span></div>${inlineCommentsHtml}`;
    }
    if (canCacheWholeUniTails && builtUniTails) {
      f._uniFastTailsEntry = {
        linesRef: rawLines,
        linesLen: rawLines.length,
        linesVer: f.lines_version,
        styleKey: uniTailsStyleKey,
        firstTxt: firstUniTxt,
        midTxt: midUniTxt,
        lastTxt: lastUniTxt,
        tails: builtUniTails,
        types: builtUniTypes,
        lastNewLn,
      };
      if (f._cachedDerivedRef) {
        if (!f._cachedDerivedRef._uniTailsByStyle) {
          f._cachedDerivedRef._uniTailsByStyle = new Map();
        }
        f._cachedDerivedRef._uniTailsByStyle.set(uniTailsStyleKey, {
          tails: builtUniTails,
          types: builtUniTypes,
          lastNewLn,
        });
      }
    }
    const isSpecialOrDeleted = f.status === "D" || f.status === "A" || f.is_binary || (f._cachedIsBin !== undefined ? f._cachedIsBin : isPgBinaryFile(f.file));
    if (!isSpecialOrDeleted && lastNewLn > 0) {
      let fileTotalLines = (typeof f.total_lines === "number" && f.total_lines > 0)
        ? f.total_lines
        : (typeof f.line_count === "number" && f.line_count > 0 ? f.line_count : 0);
      if (!fileTotalLines && hasAnyOwnProp(state.fileContentCache)) {
        const cacheKey = `${fallbackCl}:${ps || 1}:${f.file}`;
        if (Array.isArray(state.fileContentCache[cacheKey])) {
          fileTotalLines = state.fileContentCache[cacheKey].length;
        }
      }
      if (!fileTotalLines || fileTotalLines > lastNewLn) {
        const fromLine = lastNewLn + 1;
        const toLine = fileTotalLines > 0 ? fileTotalLines : -1;
        const renderTrailingFn = (typeof window !== "undefined" && typeof window.renderPgTrailingContextControlBarHtml === "function")
          ? window.renderPgTrailingContextControlBarHtml
          : renderPgTrailingContextControlBarHtml;
        html += renderTrailingFn(fallbackCl, safeFile, ps, fromLine, toLine, false);
      }
    }
    return html;
  }

  function resolveFastDiffLineHtml(ln, idx, lineType, intralineByIdx, hlCachePrefix, isCanonHlPrefix, langId, scheme, showTabs, showWsErrors) {
    const intra = idx != null ? intralineByIdx[idx] : null;
    const text = ln.text || "";
    if (!intra && hlCachePrefix && text.length <= 512) {
      if (ln._hlPrefix === hlCachePrefix && ln._hlText === text && ln._hlHtml !== undefined) {
        return ln._hlHtml;
      }
      const key = isCanonHlPrefix ? text : hlCachePrefix + text;
      let html = _pgHighlightedLineHtmlCache.get(key);
      if (html === undefined) {
        html = renderAndCacheSyntaxLineHtml(text, langId, scheme, showTabs, showWsErrors, key);
      }
      ln._hlPrefix = hlCachePrefix;
      ln._hlText = text;
      ln._hlHtml = html;
      return html;
    }
    if (intra && ln._intraRef === intra && ln._hlPrefix === hlCachePrefix && ln._hlText === text && ln._intraHtml !== undefined) {
      return ln._intraHtml;
    }
    const html = renderHighlightedDiffLineHtml(text, lineType, langId, scheme, intra);
    if (intra) {
      ln._intraRef = intra;
      ln._hlPrefix = hlCachePrefix;
      ln._hlText = text;
      ln._intraHtml = html;
    }
    return html;
  }

  function renderFileDiffSideBySideHtml(
    f,
    fallbackCl,
    _ps,
    safeFile,
    fileComments,
    scheme,
    langInfo,
    intralineByIdx,
    _maxDigits
  ) {
    const rawLines = Array.isArray(f.lines) ? f.lines : [];
    if (rawLines.length === 0) return "";
    const ps = _ps || 1;
    const fileBlameList = hasAnyOwnProp(state.fileBlameCache)
      ? (state.fileBlameCache[`${fallbackCl}:${ps}:${safeFile}`] || null)
      : null;
    const blameMap = fileBlameList ? buildPgLineBlameMap(fileBlameList) : null;
    const blameHunkHtml = renderPgBlameCellHtml(null, 0, true);
    const emptyBlameCellHtml = renderPgBlameCellHtml(null, 0, false);
    const prefs = getPgDiffPreferences();
    const checkWsIgnore = Boolean(prefs && prefs.ignoreWhitespace !== "none");
    const showTabs = Boolean(prefs && prefs.showTabs);
    const showWsErrors = !prefs || prefs.showWhitespaceErrors !== false;
    const hasFileComments = Array.isArray(fileComments) && fileComments.length > 0;
    const commentLineIdx = hasFileComments ? buildPgFileCommentsLineIndex(fileComments) : null;
    const jsSafeFile = escapeJsQuote(safeFile);
    const baseClNum = Number(fallbackCl || 0);
    const hlSchemeKey = (scheme === "intraline_focus" || scheme === "semantic_review") ? scheme : "syntax_intraline";
    const hlCachePrefix = (scheme !== "classic" && (!prefs || prefs.syntaxHighlighting !== false))
      ? `${langInfo.id}\x00${hlSchemeKey}\x00${showTabs ? 1 : 0}${showWsErrors ? 1 : 0}1\x00`
      : "";
    const isCanonHlPrefix = hlCachePrefix === _CANONICAL_CPP_HL_PREFIX;
    const fastRowOpenPrefix = `<div class="code-line code-line-sbs  " data-cl="${baseClNum}" data-file="${safeFile}" data-idx="`;
    const canFastSbsRow = !hasFileComments && !blameMap && !checkWsIgnore;
    let canCacheWholeSbsTails = canFastSbsRow && rawLines.length >= 8 && emptyBlameCellHtml === _DEFAULT_EMPTY_BLAME_CELL_HTML;
    const sbsTailsStyleKey = canCacheWholeSbsTails
      ? `${langInfo.id}\x00${scheme}\x00${showTabs ? 1 : 0}${showWsErrors ? 1 : 0}\x00${intralineByIdx && hasAnyOwnProp(intralineByIdx) ? 1 : 0}`
      : "";
    const firstSbsTxt = canCacheWholeSbsTails ? (rawLines[0].text || "") : "";
    const midSbsTxt = canCacheWholeSbsTails ? (rawLines[rawLines.length >> 1].text || "") : "";
    const lastSbsTxt = canCacheWholeSbsTails ? (rawLines[rawLines.length - 1].text || "") : "";
    let lastNewLn = 0;
    let renderedRows = "";

    function appendSbsRow(rowType, leftLine, leftIdx, rightLine, rightIdx, effectiveIdx) {
      canCacheWholeSbsTails = false;
      const oldVal = leftLine ? leftLine.old : null;
      const newVal = rightLine ? rightLine.new : null;
      const oldStr = oldVal != null ? String(oldVal) : "";
      const newStr = newVal != null ? String(newVal) : "";
      if (newStr !== "") {
        const parsedNew = typeof newVal === "number" ? newVal : parseInt(newStr, 10);
        if (parsedNew > lastNewLn) {
          lastNewLn = parsedNew;
        }
      }

      const leftCls = !leftLine ? "side-blank" : (leftLine.type === "del" ? "side-del" : "side-ctx");
      const rightCls = !rightLine ? "side-blank" : (rightLine.type === "add" ? "side-add" : "side-ctx");

      const lineCl = (rightLine && rightLine.clNum) ? Number(rightLine.clNum) : ((leftLine && leftLine.clNum) ? Number(leftLine.clNum) : baseClNum);

      let threadCls = "";
      let inlineCommentsHtml = "";
      let leftGutterBadge = "";
      let rightGutterBadge = "";
      let leftHasComment = "";
      let rightHasComment = "";
      if (hasFileComments) {
        const oldLnNum = oldStr !== "" ? (typeof oldVal === "number" ? oldVal : parseInt(oldStr, 10)) : 0;
        const newLnNum = newStr !== "" ? (typeof newVal === "number" ? newVal : parseInt(newStr, 10)) : 0;
        const leftMatch = commentLineIdx.hasIdxRange || (oldLnNum > 0 && commentLineIdx.leftLines.has(oldLnNum));
        const rightMatch = commentLineIdx.hasIdxRange || (newLnNum > 0 && commentLineIdx.rightLines.has(newLnNum));
        if (leftMatch || rightMatch) {
          threadCls = getLineThreadIndicatorClass(fileComments, oldStr, newStr, null, effectiveIdx);
          inlineCommentsHtml = renderInlineLineCommentsHtml(lineCl, f.file, oldStr, newStr, fileComments, null, effectiveIdx);
          const leftGutterSummary = (leftMatch && leftLine && oldStr)
            ? getPgLineCommentSummary(fileComments, oldStr, "left", leftIdx != null ? leftIdx : effectiveIdx, f.file)
            : null;
          const rightGutterSummary = (rightMatch && rightLine && newStr)
            ? getPgLineCommentSummary(fileComments, newStr, "right", rightIdx != null ? rightIdx : effectiveIdx, f.file)
            : null;
          leftGutterBadge = leftGutterSummary ? renderPgGutterCommentBadgeHtml(leftGutterSummary, lineCl, f.file, "left", oldStr) : "";
          rightGutterBadge = rightGutterSummary ? renderPgGutterCommentBadgeHtml(rightGutterSummary, lineCl, f.file, "right", newStr) : "";
          leftHasComment = leftGutterSummary ? "has-comment-badge" : "";
          rightHasComment = rightGutterSummary ? "has-comment-badge" : "";
        }
      }

      let codeLeftInnerHtml = "";
      if (leftLine) {
        codeLeftInnerHtml = resolveFastDiffLineHtml(
          leftLine, leftIdx, leftLine.type || "ctx", intralineByIdx,
          hlCachePrefix, isCanonHlPrefix, langInfo.id, scheme, showTabs, showWsErrors
        );
      }

      let codeRightInnerHtml = "";
      if (rightLine === leftLine && rightIdx === leftIdx) {
        codeRightInnerHtml = codeLeftInnerHtml;
      } else if (rightLine) {
        codeRightInnerHtml = resolveFastDiffLineHtml(
          rightLine, rightIdx, rightLine.type || "ctx", intralineByIdx,
          hlCachePrefix, isCanonHlPrefix, langInfo.id, scheme, showTabs, showWsErrors
        );
      }

      const safeOldStr = (typeof oldVal === "number" || oldStr === "") ? oldStr : escapeHtml(oldStr);
      const safeNewStr = (typeof newVal === "number" || newStr === "") ? newStr : escapeHtml(newStr);
      const leftGutterHtml = leftLine
        ? `<span class="ln ln-old ${leftHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="${leftIdx != null ? leftIdx : effectiveIdx}"${_SBS_LEFT_GUTTER_MID}${safeOldStr}${_SBS_LEFT_GUTTER_BTN}${leftGutterBadge}${safeOldStr}</span>`
        : _BLANK_OLD_GUTTER_HTML;

      const rightGutterHtml = rightLine
        ? `<span class="ln ln-new ${rightHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="${rightIdx != null ? rightIdx : effectiveIdx}"${_SBS_RIGHT_GUTTER_MID}${safeNewStr}${_SBS_RIGHT_GUTTER_BTN}${rightGutterBadge}${safeNewStr}</span>`
        : _BLANK_NEW_GUTTER_HTML;

      const wsCls = (checkWsIgnore && leftLine && rightLine && leftLine !== rightLine && isWhitespaceDiffOnly(leftLine.text, rightLine.text))
        ? "pg-ws-ignored"
        : "";
      let blameCellHtml = emptyBlameCellHtml;
      let blameAttr = "";
      if (blameMap) {
        const targetLineNum = oldStr ? (typeof oldVal === "number" ? oldVal : parseInt(oldStr, 10)) : (newStr ? (typeof newVal === "number" ? newVal : parseInt(newStr, 10)) : 0);
        const blameEntry = targetLineNum ? blameMap.byLine[targetLineNum] : null;
        if (blameEntry) {
          blameCellHtml = renderPgBlameCellHtml(blameEntry, targetLineNum, false);
          blameAttr = ` data-blame-id="${escapeHtml(blameEntry.shortSha)}"`;
        }
      }

      renderedRows += `<div class="code-line code-line-sbs ${threadCls} ${wsCls}" data-cl="${lineCl}" data-file="${safeFile}" data-idx="${effectiveIdx}" data-left-idx="${leftIdx != null ? leftIdx : ""}" data-right-idx="${rightIdx != null ? rightIdx : ""}" data-old-ln="${safeOldStr}" data-new-ln="${safeNewStr}" data-type="${rowType}"${blameAttr}>${blameCellHtml}${leftGutterHtml}<span class="code-text code-side-cell code-side-left ${leftCls}">${codeLeftInnerHtml}</span>${rightGutterHtml}<span class="code-text code-side-cell code-side-right ${rightCls}">${codeRightInnerHtml}</span></div>${inlineCommentsHtml}`;
    }

    let i = 0;
    const n = rawLines.length;
    if (canCacheWholeSbsTails) {
      let cachedSbsObj = null;
      const fSbsEntry = f._sbsFastTailsEntry;
      if (
        fSbsEntry &&
        fSbsEntry.linesRef === rawLines &&
        fSbsEntry.linesLen === n &&
        fSbsEntry.linesVer === f.lines_version &&
        fSbsEntry.styleKey === sbsTailsStyleKey &&
        fSbsEntry.firstTxt === firstSbsTxt &&
        fSbsEntry.midTxt === midSbsTxt &&
        fSbsEntry.lastTxt === lastSbsTxt
      ) {
        cachedSbsObj = fSbsEntry;
      } else if (f._cachedDerivedRef && f._cachedDerivedRef._sbsTailsByStyle) {
        cachedSbsObj = f._cachedDerivedRef._sbsTailsByStyle.get(sbsTailsStyleKey) || null;
        if (cachedSbsObj) {
          f._sbsFastTailsEntry = {
            linesRef: rawLines,
            linesLen: n,
            linesVer: f.lines_version,
            styleKey: sbsTailsStyleKey,
            firstTxt: firstSbsTxt,
            midTxt: midSbsTxt,
            lastTxt: lastSbsTxt,
            tails: cachedSbsObj.tails,
            lastNewLn: cachedSbsObj.lastNewLn,
          };
        }
      }
      if (cachedSbsObj) {
        const tails = cachedSbsObj.tails;
        for (let r = 0; r < tails.length; r++) {
          renderedRows += fastRowOpenPrefix + tails[r];
        }
        lastNewLn = cachedSbsObj.lastNewLn;
        i = n;
      }
    }
    const builtSbsTails = (canCacheWholeSbsTails && i === 0) ? [] : null;
    while (i < n) {
      const ln = rawLines[i];
      const t = ln.type || "ctx";
      if (t === "ctx" && canFastSbsRow && !ln.clNum) {
        const oldVal = ln.old;
        const newVal = ln.new;
        const safeOldStr = oldVal != null ? (typeof oldVal === "number" ? oldVal : (oldVal === "" ? "" : escapeHtml(String(oldVal)))) : "";
        const safeNewStr = newVal != null ? (typeof newVal === "number" ? newVal : (newVal === "" ? "" : escapeHtml(String(newVal)))) : "";
        if (safeNewStr !== "") {
          const parsedNew = typeof newVal === "number" ? newVal : parseInt(safeNewStr, 10);
          if (parsedNew > lastNewLn) lastNewLn = parsedNew;
        }
        const lText = ln.text || "";
        let codeHtml;
        if (ln._hlPrefix === hlCachePrefix && ln._hlText === lText && ln._hlHtml !== undefined) {
          codeHtml = ln._hlHtml;
        } else if (hlCachePrefix && lText.length <= 512) {
          const lKey = isCanonHlPrefix ? lText : hlCachePrefix + lText;
          codeHtml = _pgHighlightedLineHtmlCache.get(lKey);
          if (codeHtml === undefined) {
            codeHtml = renderAndCacheSyntaxLineHtml(lText, langInfo.id, scheme, showTabs, showWsErrors, lKey);
          }
          ln._hlPrefix = hlCachePrefix;
          ln._hlText = lText;
          ln._hlHtml = codeHtml;
        } else {
          codeHtml = renderHighlightedDiffLineHtml(lText, "ctx", langInfo.id, scheme, null);
        }
        const rowTail = buildFastSbsCtxRowTail(ln, i, oldVal, newVal, safeOldStr, safeNewStr, emptyBlameCellHtml, codeHtml);
        renderedRows += fastRowOpenPrefix + rowTail;
        if (canCacheWholeSbsTails && builtSbsTails) {
          builtSbsTails.push(rowTail);
        }
        i++;
        continue;
      }
      if (t === "hunk") {
        canCacheWholeSbsTails = false;
        const m = String(ln.text || "").match(_DIFF_HUNK_NEW_START_RE);
        const hunkNewStart = m ? parseInt(m[1], 10) : 0;
        let ctxBarHtml = "";
        let fromLine = 0;
        let toLine = 0;
        if (hunkNewStart > lastNewLn + 1) {
          fromLine = lastNewLn + 1;
          toLine = hunkNewStart - 1;
          ctxBarHtml = renderPgContextControlBarHtml(fallbackCl, safeFile, ps, fromLine, toLine, true);
        }
        if (hunkNewStart > 0 && lastNewLn < hunkNewStart - 1) {
          lastNewLn = hunkNewStart - 1;
        }
        const actionsHtml = renderPgHunkExpandActionsHtml(fallbackCl, safeFile, ps, fromLine, toLine, ln.old, ln.new);
        const hunkRowHtml = `<div class="code-line code-hunk code-line-sbs" data-type="hunk" data-cl="${fallbackCl}" data-file="${safeFile}" data-ps="${ps}" data-from-line="${fromLine}" data-to-line="${toLine}" data-idx="${i}" tabindex="0" role="region" aria-label="Diff hunk line ${escapeHtml(ln.new || ln.old || "")}" onkeydown="onPgHunkHeaderKeyDown(event, ${fallbackCl}, '${jsSafeFile}', ${ps})">${blameHunkHtml}<div class="hunk-header-bar">${renderHunkHeaderInnerHtml(ln.text, scheme)}${actionsHtml}</div></div>`;
        renderedRows += ctxBarHtml ? ctxBarHtml + hunkRowHtml : hunkRowHtml;
        i++;
        continue;
      }
      if (t === "ctx") {
        appendSbsRow("ctx", ln, i, ln, i, i);
        i++;
        continue;
      }

      // Modification block: pair consecutive dels and adds without wrapper array allocations
      const delStart = i;
      while (i < n && rawLines[i].type === "del") {
        i++;
      }
      const delCount = i - delStart;
      const addStart = i;
      while (i < n && rawLines[i].type === "add") {
        i++;
      }
      const addCount = i - addStart;
      const count = delCount > addCount ? delCount : addCount;
      if (count === 0) {
        appendSbsRow("ctx", ln, i, ln, i, i);
        i++;
        continue;
      }
      for (let k = 0; k < count; k++) {
        const lIdx = k < delCount ? delStart + k : null;
        const rIdx = k < addCount ? addStart + k : null;
        const lLine = lIdx != null ? rawLines[lIdx] : null;
        const rLine = rIdx != null ? rawLines[rIdx] : null;
        const primaryIdx = rIdx != null ? rIdx : lIdx;
        if (canFastSbsRow && (!lLine || !lLine.clNum) && (!rLine || !rLine.clNum)) {
          const oldVal = lLine ? lLine.old : null;
          const newVal = rLine ? rLine.new : null;
          const safeOldStr = oldVal != null ? (typeof oldVal === "number" ? oldVal : (oldVal === "" ? "" : escapeHtml(String(oldVal)))) : "";
          const safeNewStr = newVal != null ? (typeof newVal === "number" ? newVal : (newVal === "" ? "" : escapeHtml(String(newVal)))) : "";
          if (safeNewStr !== "") {
            const parsedNew = typeof newVal === "number" ? newVal : parseInt(safeNewStr, 10);
            if (parsedNew > lastNewLn) lastNewLn = parsedNew;
          }
          const codeLeftHtml = lLine
            ? resolveFastDiffLineHtml(lLine, lIdx, "del", intralineByIdx, hlCachePrefix, isCanonHlPrefix, langInfo.id, scheme, showTabs, showWsErrors)
            : "";
          const codeRightHtml = rLine
            ? resolveFastDiffLineHtml(rLine, rIdx, "add", intralineByIdx, hlCachePrefix, isCanonHlPrefix, langInfo.id, scheme, showTabs, showWsErrors)
            : "";
          const rowTail = buildFastSbsDiffRowTail(
            rLine || lLine, primaryIdx, lLine, lIdx, rLine, rIdx,
            oldVal, newVal, safeOldStr, safeNewStr, emptyBlameCellHtml, codeLeftHtml, codeRightHtml
          );
          renderedRows += fastRowOpenPrefix + rowTail;
          if (canCacheWholeSbsTails && builtSbsTails) {
            builtSbsTails.push(rowTail);
          }
        } else {
          appendSbsRow("diff", lLine, lIdx, rLine, rIdx, primaryIdx);
        }
      }
    }
    if (canCacheWholeSbsTails && builtSbsTails) {
      f._sbsFastTailsEntry = {
        linesRef: rawLines,
        linesLen: n,
        linesVer: f.lines_version,
        styleKey: sbsTailsStyleKey,
        firstTxt: firstSbsTxt,
        midTxt: midSbsTxt,
        lastTxt: lastSbsTxt,
        tails: builtSbsTails,
        lastNewLn,
      };
      if (f._cachedDerivedRef) {
        if (!f._cachedDerivedRef._sbsTailsByStyle) {
          f._cachedDerivedRef._sbsTailsByStyle = new Map();
        }
        f._cachedDerivedRef._sbsTailsByStyle.set(sbsTailsStyleKey, {
          tails: builtSbsTails,
          lastNewLn,
        });
      }
    }

    const isSpecialOrDeleted = f.status === "D" || f.status === "A" || f.is_binary || (f._cachedIsBin !== undefined ? f._cachedIsBin : isPgBinaryFile(f.file));
    let trailingCtxHtml = "";
    if (!isSpecialOrDeleted && lastNewLn > 0) {
      let fileTotalLines = (typeof f.total_lines === "number" && f.total_lines > 0)
        ? f.total_lines
        : (typeof f.line_count === "number" && f.line_count > 0 ? f.line_count : 0);
      if (!fileTotalLines && hasAnyOwnProp(state.fileContentCache)) {
        const cacheKey = `${fallbackCl}:${ps || 1}:${f.file}`;
        if (Array.isArray(state.fileContentCache[cacheKey])) {
          fileTotalLines = state.fileContentCache[cacheKey].length;
        }
      }
      if (!fileTotalLines || fileTotalLines > lastNewLn) {
        const fromLine = lastNewLn + 1;
        const toLine = fileTotalLines > 0 ? fileTotalLines : -1;
        const renderTrailingFn = (typeof window !== "undefined" && typeof window.renderPgTrailingContextControlBarHtml === "function")
          ? window.renderPgTrailingContextControlBarHtml
          : renderPgTrailingContextControlBarHtml;
        trailingCtxHtml = renderTrailingFn(fallbackCl, safeFile, ps, fromLine, toLine, true);
      }
    }
    return renderedRows + trailingCtxHtml;
  }

  const PG_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "avif", "tiff", "tif"]);
  const PG_BINARY_EXTENSIONS = new Set([
    "pdf", "zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar",
    "jar", "apk", "aab", "aar", "dex", "class", "pyc",
    "so", "dll", "dylib", "exe", "bin", "dat", "o", "a", "lib",
    "wasm", "ttf", "otf", "woff", "woff2",
    "mp3", "mp4", "wav", "ogg", "webm", "mov", "avi",
    "pb", "sqlite", "db"
  ]);
  if (typeof window !== "undefined") {
    window.PG_IMAGE_EXTENSIONS = PG_IMAGE_EXTENSIONS;
    window.PG_BINARY_EXTENSIONS = PG_BINARY_EXTENSIONS;
  }

  function getPgCleanFileExtension(filePath) {
    const ext = (typeof getPgFileExtension === "function") ? getPgFileExtension(filePath) : "";
    if (!ext) return "";
    return ext.charCodeAt(0) === 46 ? ext.slice(1) : ext;
  }

  function isPgImageFile(filePath) {
    const ext = getPgCleanFileExtension(filePath);
    return ext ? PG_IMAGE_EXTENSIONS.has(ext) : false;
  }

  function isPgBinaryFile(filePath) {
    const ext = getPgCleanFileExtension(filePath);
    return ext ? (PG_IMAGE_EXTENSIONS.has(ext) || PG_BINARY_EXTENSIONS.has(ext)) : false;
  }

  function getPgImageFormat(filePath) {
    const ext = getPgCleanFileExtension(filePath);
    return ext ? ext.toUpperCase() : "IMAGE";
  }

  function renderPgImageViewerHtml(f, cl, ps, safeFile, safeFileId, status, isCollapsed) {
    const displayStyle = isCollapsed ? ' style="display:none;"' : "";
    const format = getPgImageFormat(f.file);
    const isAdded = status === "A";
    const isDeleted = status === "D";

    return `<div class="pg-image-viewer" id="pgImageViewer-${cl}-${safeFileId}" data-cl="${cl}" data-ps="${ps}" data-file="${safeFile}" data-status="${escapeHtml(status)}"${displayStyle}>
      <div class="pg-image-viewer-toolbar">
        <div class="pg-image-toolbar-left">
          <div class="pg-image-mode-group" id="pgImageModeGroup-${cl}-${safeFileId}" role="group" aria-label="Image comparison mode">
            <button type="button" class="pg-image-mode-btn is-active" data-mode="2-up" onclick="setPgImageDiffMode(${cl}, '${safeFileId}', '2-up')" title="2-up: Side-by-side comparison">
              <span class="pg-mode-icon">☷</span> <span>2-up</span>
            </button>
            <button type="button" class="pg-image-mode-btn" data-mode="swipe" onclick="setPgImageDiffMode(${cl}, '${safeFileId}', 'swipe')" title="Swipe: Drag slider or image stage to compare">
              <span class="pg-mode-icon">◨</span> <span>Swipe</span>
            </button>
            <button type="button" class="pg-image-mode-btn" data-mode="onion" onclick="setPgImageDiffMode(${cl}, '${safeFileId}', 'onion')" title="Onion skin: Opacity crossfade &amp; blink">
              <span class="pg-mode-icon">◐</span> <span>Onion skin</span>
            </button>
          </div>
          <div class="pg-image-zoom-group" id="pgImageZoomGroup-${cl}-${safeFileId}" role="group" aria-label="Image zoom">
            <button type="button" class="pg-image-zoom-btn is-active" data-zoom="fit" onclick="setPgImageZoom(${cl}, '${safeFileId}', 'fit')" title="Fit to container">Fit</button>
            <button type="button" class="pg-image-zoom-btn" data-zoom="100" onclick="setPgImageZoom(${cl}, '${safeFileId}', '100')" title="Original size (1:1)">100%</button>
            <button type="button" class="pg-image-zoom-btn" data-zoom="200" onclick="setPgImageZoom(${cl}, '${safeFileId}', '200')" title="Zoom in 2x (Pixelated)">200%</button>
            <button type="button" class="pg-image-zoom-btn" data-zoom="400" onclick="setPgImageZoom(${cl}, '${safeFileId}', '400')" title="Zoom in 4x (Pixelated)">400%</button>
          </div>
          <div class="pg-image-bg-group" id="pgImageBgGroup-${cl}-${safeFileId}" role="group" aria-label="Image background">
            <button type="button" class="pg-image-bg-btn is-active" data-bg="checkerboard" onclick="setPgImageBackground(${cl}, '${safeFileId}', 'checkerboard')" title="Checkerboard background (Transparent)">🏁 Check</button>
            <button type="button" class="pg-image-bg-btn" data-bg="white" onclick="setPgImageBackground(${cl}, '${safeFileId}', 'white')" title="Solid white background">⬜ White</button>
            <button type="button" class="pg-image-bg-btn" data-bg="dark" onclick="setPgImageBackground(${cl}, '${safeFileId}', 'dark')" title="Solid dark background">⬛ Dark</button>
          </div>
          <div class="pg-image-pan-group" id="pgImagePanGroup-${cl}-${safeFileId}" role="group" aria-label="Image pan &amp; sync">
            <button type="button" class="pg-image-sync-btn is-active" id="pgImageSyncBtn-${cl}-${safeFileId}" onclick="togglePgImageSyncPan(${cl}, '${safeFileId}')" title="Synchronize pan and scrolling across Base and Patchset (Toggle)">
              <span class="pg-sync-icon">🔗</span> <span class="pg-sync-label">Sync Pan: ON</span>
            </button>
            <button type="button" class="pg-image-center-btn" onclick="centerPgImagePan(${cl}, '${safeFileId}')" title="Center images in viewport">
              <span>⌖ Center</span>
            </button>
          </div>
        </div>
        <div class="pg-image-toolbar-right">
          <span class="pg-image-format-badge">${escapeHtml(format)}</span>
          <span class="pg-image-dim-pill" id="pgImageDimPill-${cl}-${safeFileId}">Loading dimensions…</span>
          <span class="pg-image-coords-badge" id="pgImageCoords-${cl}-${safeFileId}" title="Hover over image to inspect pixel coordinates">X: — · Y: —</span>
          <button type="button" class="pg-btn-subtle" onclick="reloadPgImageData(${cl}, ${ps}, '${safeFile}', '${safeFileId}', '${escapeHtml(status)}')" title="Reload images">🔄</button>
        </div>
      </div>
      <div class="pg-image-viewport pg-checkerboard-bg bg-checkerboard zoom-fit mode-2up" id="pgImageViewport-${cl}-${safeFileId}">
        <!-- 2-up comparison -->
        <div class="pg-image-2up-grid" id="pgImage2up-${cl}-${safeFileId}">
          <div class="pg-image-frame frame-base">
            <div class="pg-image-frame-header">
              <span class="pg-frame-tag tag-base">Base (Old)</span>
              <span class="pg-frame-dim" id="pgImageBaseDim-${cl}-${safeFileId}">${isAdded ? "N/A" : "Loading…"}</span>
            </div>
            <div class="pg-image-frame-body can-pan" id="pgImageFrameBase-${cl}-${safeFileId}" data-cl="${cl}" data-file-id="${safeFileId}" data-side="base" onscroll="onPgImageFrameScroll(event, ${cl}, '${safeFileId}', 'base')" onmousedown="onPgImagePanMouseDown(event, ${cl}, '${safeFileId}', 'base')" onmouseleave="onPgImageMouseLeave(${cl}, '${safeFileId}')">
              ${isAdded
                ? `<div class="pg-image-frame-empty">(No base file · File added)</div>`
                : `<div class="pg-image-stage" id="pgImageStageBase-${cl}-${safeFileId}" onmousemove="onPgImageMouseMove(event, ${cl}, '${safeFileId}', 'base')">
                    <img class="pg-diff-img img-base" id="pgImgBase-${cl}-${safeFileId}" alt="Base: ${safeFile}" onload="onPgImageLoaded(${cl}, '${safeFileId}', 'base')" onerror="onPgImageError(${cl}, '${safeFileId}', 'base')" />
                    <div class="pg-image-crosshair" id="pgImageCrosshairBase-${cl}-${safeFileId}" style="display:none;"><div class="pg-crosshair-h"></div><div class="pg-crosshair-v"></div></div>
                  </div>`
              }
            </div>
          </div>
          <div class="pg-image-frame frame-target">
            <div class="pg-image-frame-header">
              <span class="pg-frame-tag tag-target">Patchset ${ps} (New)</span>
              <span class="pg-frame-dim" id="pgImageTargetDim-${cl}-${safeFileId}">${isDeleted ? "N/A" : "Loading…"}</span>
            </div>
            <div class="pg-image-frame-body can-pan" id="pgImageFrameTarget-${cl}-${safeFileId}" data-cl="${cl}" data-file-id="${safeFileId}" data-side="target" onscroll="onPgImageFrameScroll(event, ${cl}, '${safeFileId}', 'target')" onmousedown="onPgImagePanMouseDown(event, ${cl}, '${safeFileId}', 'target')" onmouseleave="onPgImageMouseLeave(${cl}, '${safeFileId}')">
              ${isDeleted
                ? `<div class="pg-image-frame-empty">(File deleted)</div>`
                : `<div class="pg-image-stage" id="pgImageStageTarget-${cl}-${safeFileId}" onmousemove="onPgImageMouseMove(event, ${cl}, '${safeFileId}', 'target')">
                    <img class="pg-diff-img img-target" id="pgImgTarget-${cl}-${safeFileId}" alt="Patchset ${ps}: ${safeFile}" onload="onPgImageLoaded(${cl}, '${safeFileId}', 'target')" onerror="onPgImageError(${cl}, '${safeFileId}', 'target')" />
                    <div class="pg-image-crosshair" id="pgImageCrosshairTarget-${cl}-${safeFileId}" style="display:none;"><div class="pg-crosshair-h"></div><div class="pg-crosshair-v"></div></div>
                  </div>`
              }
            </div>
          </div>
        </div>

        <!-- Swipe comparison -->
        <div class="pg-image-swipe-wrap" id="pgImageSwipe-${cl}-${safeFileId}" style="display:none;">
          <div class="pg-image-swipe-stage" id="pgImageSwipeStage-${cl}-${safeFileId}"
               onpointerdown="onPgImageSwipePointerDown(event, ${cl}, '${safeFileId}')"
               onpointermove="onPgImageSwipePointerMove(event, ${cl}, '${safeFileId}')"
               onpointerup="onPgImageSwipePointerUp(event, ${cl}, '${safeFileId}')"
               onpointercancel="onPgImageSwipePointerUp(event, ${cl}, '${safeFileId}')"
               title="Click or drag anywhere on image to scrub swipe divider">
            <img class="pg-diff-img img-swipe-base" id="pgImgSwipeBase-${cl}-${safeFileId}" alt="Base swipe layer" />
            <div class="pg-image-swipe-clip" id="pgImageSwipeClip-${cl}-${safeFileId}" style="clip-path: polygon(0 0, 50% 0, 50% 100%, 0 100%);">
              <img class="pg-diff-img img-swipe-target" id="pgImgSwipeTarget-${cl}-${safeFileId}" alt="Target swipe layer" />
            </div>
            <div class="pg-image-swipe-divider" id="pgImageSwipeDivider-${cl}-${safeFileId}" style="left: 50%;">
              <div class="pg-image-swipe-handle" title="Drag to swipe">◄||►</div>
            </div>
          </div>
          <div class="pg-image-slider-control">
            <label>Swipe:</label>
            <input type="range" min="0" max="100" value="50" class="pg-image-range-input pg-image-swipe-range" oninput="onPgImageSwipeInput(${cl}, '${safeFileId}', this.value)" aria-label="Swipe divider percentage" />
            <span class="pg-image-slider-val" id="pgImageSwipeVal-${cl}-${safeFileId}">50%</span>
            <div class="pg-image-preset-group" role="group" aria-label="Swipe presets">
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageSwipeInput(${cl}, '${safeFileId}', 0)" title="0% (Base only)">0%</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageSwipeInput(${cl}, '${safeFileId}', 25)" title="25% divider">25%</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageSwipeInput(${cl}, '${safeFileId}', 50)" title="50% center">50%</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageSwipeInput(${cl}, '${safeFileId}', 75)" title="75% divider">75%</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageSwipeInput(${cl}, '${safeFileId}', 100)" title="100% (Patchset only)">100%</button>
            </div>
            <button type="button" class="pg-image-preset-btn" onclick="flipPgImageSides(${cl}, '${safeFileId}')" title="Flip between Base (0%) and Patchset (100%) (Space)">⇄ Flip</button>
          </div>
        </div>

        <!-- Onion skin comparison -->
        <div class="pg-image-onion-wrap" id="pgImageOnion-${cl}-${safeFileId}" style="display:none;">
          <div class="pg-image-onion-stage" id="pgImageOnionStage-${cl}-${safeFileId}"
               onpointerdown="onPgImageOnionPointerDown(event, ${cl}, '${safeFileId}')"
               onpointermove="onPgImageOnionPointerMove(event, ${cl}, '${safeFileId}')"
               onpointerup="onPgImageOnionPointerUp(event, ${cl}, '${safeFileId}')"
               onpointercancel="onPgImageOnionPointerUp(event, ${cl}, '${safeFileId}')"
               title="Click or drag horizontally on image to scrub opacity">
            <img class="pg-diff-img img-onion-base" id="pgImgOnionBase-${cl}-${safeFileId}" alt="Base onion layer" />
            <img class="pg-diff-img img-onion-target" id="pgImgOnionTarget-${cl}-${safeFileId}" style="opacity: 0.5;" alt="Target onion layer" />
          </div>
          <div class="pg-image-slider-control">
            <label>Opacity (Patchset ${ps}):</label>
            <input type="range" min="0" max="100" value="50" class="pg-image-range-input pg-image-onion-range" oninput="onPgImageOnionInput(${cl}, '${safeFileId}', this.value)" aria-label="Patchset opacity percentage" />
            <span class="pg-image-slider-val" id="pgImageOnionVal-${cl}-${safeFileId}">50%</span>
            <div class="pg-image-preset-group" role="group" aria-label="Opacity presets">
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageOnionInput(${cl}, '${safeFileId}', 0)" title="Base only (0% new)">0% (Base)</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageOnionInput(${cl}, '${safeFileId}', 25)" title="25% new">25%</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageOnionInput(${cl}, '${safeFileId}', 50)" title="50% crossfade">50%</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageOnionInput(${cl}, '${safeFileId}', 75)" title="75% new">75%</button>
              <button type="button" class="pg-image-preset-btn" onclick="onPgImageOnionInput(${cl}, '${safeFileId}', 100)" title="Patchset ${ps} only (100% new)">100% (PS ${ps})</button>
            </div>
            <button type="button" id="btnImageBlink-${cl}-${safeFileId}" class="pg-btn-image-blink" onclick="togglePgImageBlink(${cl}, '${safeFileId}')" title="Toggle automatic blink comparator (1.5Hz) to spot subtle pixel differences (Space)">
              <span class="pg-btn-icon">⏯</span> <span>Blink</span>
            </button>
            <button type="button" class="pg-image-preset-btn" onclick="flipPgImageSides(${cl}, '${safeFileId}')" title="Flip between Base (0%) and Patchset (100%) (Space)">⇄ Flip</button>
          </div>
        </div>
      </div>
    </div>`;
  }

  function renderPgBinaryFileHtml(f, cl, ps, safeFile, safeFileId, status, isCollapsed) {
    const displayStyle = isCollapsed ? ' style="display:none;"' : "";
    const statusDesc = status === "A" ? "Added" : (status === "D" ? "Deleted" : (status === "R" ? "Renamed" : "Modified"));
    const isAdded = status === "A";

    return `<div class="pg-binary-file-card" id="pgBinaryFile-${cl}-${safeFileId}" data-cl="${cl}" data-ps="${ps}" data-file="${safeFile}" data-status="${escapeHtml(status)}"${displayStyle}>
      <div class="pg-binary-icon">📦</div>
      <div class="pg-binary-body">
        <div class="pg-binary-title">Binary file (${statusDesc})</div>
        <div class="pg-binary-sub">Differences cannot be rendered inline for this binary file.</div>
        <div class="pg-binary-actions">
          <button type="button" class="pg-btn-binary-download" onclick="downloadPgBinaryFile(${cl}, '${safeFile}', ${ps}, false)">
            <span>⬇</span> <span>Download File (Patchset ${ps})</span>
          </button>
          ${!isAdded ? `<button type="button" class="pg-btn-binary-download" onclick="downloadPgBinaryFile(${cl}, '${safeFile}', ${ps}, true)">
            <span>⬇</span> <span>Download Base</span>
          </button>` : ""}
        </div>
      </div>
    </div>`;
  }

  function setPgImageDiffMode(cl, fileId, mode) {
    const viewport = document.getElementById(`pgImageViewport-${cl}-${fileId}`);
    const group = document.getElementById(`pgImageModeGroup-${cl}-${fileId}`);
    const mode2up = document.getElementById(`pgImage2up-${cl}-${fileId}`);
    const modeSwipe = document.getElementById(`pgImageSwipe-${cl}-${fileId}`);
    const modeOnion = document.getElementById(`pgImageOnion-${cl}-${fileId}`);

    if (group) {
      group.querySelectorAll(".pg-image-mode-btn").forEach(btn => {
        btn.classList.toggle("is-active", btn.getAttribute("data-mode") === mode);
      });
    }
    if (viewport) {
      viewport.classList.remove("mode-2up", "mode-swipe", "mode-onion");
      viewport.classList.add(`mode-${mode}`);
    }
    if (mode2up) mode2up.style.display = mode === "2-up" ? "grid" : "none";
    if (modeSwipe) modeSwipe.style.display = mode === "swipe" ? "flex" : "none";
    if (modeOnion) modeOnion.style.display = mode === "onion" ? "flex" : "none";

    if (mode !== "onion") {
      stopPgImageBlink(cl, fileId);
    }
  }

  function setPgImageZoom(cl, fileId, zoom) {
    const viewport = document.getElementById(`pgImageViewport-${cl}-${fileId}`);
    const group = document.getElementById(`pgImageZoomGroup-${cl}-${fileId}`);
    if (group) {
      group.querySelectorAll(".pg-image-zoom-btn").forEach(btn => {
        btn.classList.toggle("is-active", btn.getAttribute("data-zoom") === String(zoom));
      });
    }
    if (viewport) {
      viewport.classList.remove("zoom-fit", "zoom-100", "zoom-200", "zoom-400");
      viewport.classList.add(`zoom-${zoom}`);
    }
  }

  function setPgImageBackground(cl, fileId, bg) {
    const viewport = document.getElementById(`pgImageViewport-${cl}-${fileId}`);
    const group = document.getElementById(`pgImageBgGroup-${cl}-${fileId}`);
    if (group) {
      group.querySelectorAll(".pg-image-bg-btn").forEach(btn => {
        btn.classList.toggle("is-active", btn.getAttribute("data-bg") === bg);
      });
    }
    if (viewport) {
      viewport.classList.remove("bg-checkerboard", "bg-white", "bg-dark");
      viewport.classList.add(`bg-${bg}`);
    }
  }

  function cyclePgImageBackground(cl, fileId) {
    const viewport = document.getElementById(`pgImageViewport-${cl}-${fileId}`);
    if (!viewport) return;
    let curBg = "checkerboard";
    if (viewport.classList.contains("bg-white")) curBg = "white";
    else if (viewport.classList.contains("bg-dark")) curBg = "dark";

    const nextBg = curBg === "checkerboard" ? "white" : (curBg === "white" ? "dark" : "checkerboard");
    setPgImageBackground(cl, fileId, nextBg);
    if (typeof showToast === "function") {
      const bgNames = { checkerboard: "Checkerboard (Transparent)", white: "Solid White", dark: "Solid Dark" };
      showToast(`Image background: ${bgNames[nextBg] || nextBg}`);
    }
  }

  function isPgImageSyncPanEnabled(cl, fileId) {
    if (!state.imagePanSync) state.imagePanSync = {};
    const key = `${cl}:${fileId}`;
    return state.imagePanSync[key] !== false;
  }

  function togglePgImageSyncPan(cl, fileId) {
    if (!state.imagePanSync) state.imagePanSync = {};
    const key = `${cl}:${fileId}`;
    const current = state.imagePanSync[key] !== false;
    const next = !current;
    state.imagePanSync[key] = next;

    const btn = document.getElementById(`pgImageSyncBtn-${cl}-${fileId}`);
    if (btn) {
      btn.classList.toggle("is-active", next);
      const icon = btn.querySelector(".pg-sync-icon");
      const label = btn.querySelector(".pg-sync-label");
      if (icon) icon.textContent = next ? "🔗" : "🔓";
      if (label) label.textContent = next ? "Sync Pan: ON" : "Sync Pan: OFF";
      btn.setAttribute("aria-pressed", next ? "true" : "false");
    }

    if (next) {
      const frameBase = document.getElementById(`pgImageFrameBase-${cl}-${fileId}`);
      const frameTarget = document.getElementById(`pgImageFrameTarget-${cl}-${fileId}`);
      if (frameBase && frameTarget) {
        frameTarget.scrollLeft = frameBase.scrollLeft;
        frameTarget.scrollTop = frameBase.scrollTop;
      }
    }

    if (typeof showToast === "function") {
      showToast(`Image pan sync: ${next ? "ON (Locked)" : "OFF (Independent)"}`);
    }
  }

  function centerPgImagePan(cl, fileId) {
    const frameBase = document.getElementById(`pgImageFrameBase-${cl}-${fileId}`);
    const frameTarget = document.getElementById(`pgImageFrameTarget-${cl}-${fileId}`);
    if (frameBase) {
      frameBase.scrollLeft = Math.max(0, (frameBase.scrollWidth - frameBase.clientWidth) / 2);
      frameBase.scrollTop = Math.max(0, (frameBase.scrollHeight - frameBase.clientHeight) / 2);
    }
    if (frameTarget) {
      frameTarget.scrollLeft = Math.max(0, (frameTarget.scrollWidth - frameTarget.clientWidth) / 2);
      frameTarget.scrollTop = Math.max(0, (frameTarget.scrollHeight - frameTarget.clientHeight) / 2);
    }
    if (typeof showToast === "function") {
      showToast("Images centered in viewport");
    }
  }

  function cyclePgImageZoom(cl, fileId, reverse = false) {
    const effCl = cl || state.selectedClNumber;
    const viewer = fileId
      ? document.getElementById(`pgImageViewer-${effCl}-${fileId}`)
      : document.querySelector(".pg-image-viewer");
    if (!viewer) return;
    const actualCl = effCl || viewer.getAttribute("data-cl");
    const actualId = fileId || (viewer.id ? viewer.id.replace(`pgImageViewer-${actualCl}-`, "") : "");
    const viewport = document.getElementById(`pgImageViewport-${actualCl}-${actualId}`);
    if (!viewport) return;

    const modes = ["fit", "100", "200", "400"];
    let cur = "fit";
    if (viewport.classList.contains("zoom-400")) cur = "400";
    else if (viewport.classList.contains("zoom-200")) cur = "200";
    else if (viewport.classList.contains("zoom-100")) cur = "100";

    let idx = modes.indexOf(cur);
    if (idx === -1) idx = 0;
    if (reverse) {
      idx = (idx - 1 + modes.length) % modes.length;
    } else {
      idx = (idx + 1) % modes.length;
    }
    const next = modes[idx];
    setPgImageZoom(actualCl, actualId, next);

    const zoomNames = {
      fit: "Fit to container",
      "100": "100% (1:1 original size)",
      "200": "200% (2x pixelated)",
      "400": "400% (4x pixelated)",
    };
    if (typeof showToast === "function") {
      showToast(`Image zoom: ${zoomNames[next] || next}`);
    }
  }

  function updatePgImageCrosshair(cl, fileId, side, x, y, visible) {
    const crosshairEl = document.getElementById(`pgImageCrosshair${side === "base" ? "Base" : "Target"}-${cl}-${fileId}`);
    if (!crosshairEl) return;
    if (!visible) {
      crosshairEl.style.display = "none";
      return;
    }
    crosshairEl.style.display = "block";
    const hLine = crosshairEl.querySelector(".pg-crosshair-h");
    const vLine = crosshairEl.querySelector(".pg-crosshair-v");
    if (hLine) hLine.style.top = `${y}px`;
    if (vLine) vLine.style.left = `${x}px`;
  }

  function onPgImageMouseMove(e, cl, fileId, side) {
    const img = document.getElementById(`pgImg${side === "base" ? "Base" : "Target"}-${cl}-${fileId}`);
    const coordsBadge = document.getElementById(`pgImageCoords-${cl}-${fileId}`);
    if (!img) return;

    const rect = (typeof img.getBoundingClientRect === "function") ? img.getBoundingClientRect() : null;
    if (!rect || rect.width <= 0 || rect.height <= 0) return;

    const clientX = e.clientX !== undefined ? e.clientX : rect.left;
    const clientY = e.clientY !== undefined ? e.clientY : rect.top;
    const relX = clientX - rect.left;
    const relY = clientY - rect.top;

    if (relX < 0 || relX > rect.width || relY < 0 || relY > rect.height) {
      onPgImageMouseLeave(cl, fileId);
      return;
    }

    const natW = img.naturalWidth || rect.width;
    const natH = img.naturalHeight || rect.height;
    const scaleX = rect.width / (natW || 1);
    const scaleY = rect.height / (natH || 1);
    const pxX = Math.max(0, Math.min(natW - 1, Math.floor(relX / (scaleX || 1))));
    const pxY = Math.max(0, Math.min(natH - 1, Math.floor(relY / (scaleY || 1))));

    const otherSide = side === "base" ? "target" : "base";
    const otherImg = document.getElementById(`pgImg${otherSide === "base" ? "Base" : "Target"}-${cl}-${fileId}`);

    updatePgImageCrosshair(cl, fileId, side, Math.round(relX), Math.round(relY), true);
    if (otherImg) {
      const otherNatW = otherImg.naturalWidth || otherImg.clientWidth || natW;
      const otherNatH = otherImg.naturalHeight || otherImg.clientHeight || natH;
      const otherRect = (typeof otherImg.getBoundingClientRect === "function") ? otherImg.getBoundingClientRect() : null;
      if (otherRect && otherRect.width > 0) {
        const otherScaleX = otherRect.width / (otherNatW || 1);
        const otherScaleY = otherRect.height / (otherNatH || 1);
        const otherRelX = Math.round(pxX * otherScaleX);
        const otherRelY = Math.round(pxY * otherScaleY);
        updatePgImageCrosshair(cl, fileId, otherSide, otherRelX, otherRelY, true);
      }
    }

    if (coordsBadge) {
      coordsBadge.textContent = `X: ${pxX} · Y: ${pxY}`;
      coordsBadge.classList.add("is-inspecting");
    }
  }

  function onPgImageMouseLeave(cl, fileId) {
    updatePgImageCrosshair(cl, fileId, "base", 0, 0, false);
    updatePgImageCrosshair(cl, fileId, "target", 0, 0, false);
    const coordsBadge = document.getElementById(`pgImageCoords-${cl}-${fileId}`);
    if (coordsBadge) {
      coordsBadge.textContent = "X: — · Y: —";
      coordsBadge.classList.remove("is-inspecting");
    }
  }

  let _pgImageSyncScrollGuard = false;

  function onPgImageFrameScroll(e, cl, fileId, side) {
    if (_pgImageSyncScrollGuard) return;
    if (!isPgImageSyncPanEnabled(cl, fileId)) return;

    const src = e && e.target ? e.target : document.getElementById(`pgImageFrame${side === "base" ? "Base" : "Target"}-${cl}-${fileId}`);
    if (!src) return;
    const dstId = side === "base"
      ? `pgImageFrameTarget-${cl}-${fileId}`
      : `pgImageFrameBase-${cl}-${fileId}`;
    const dst = document.getElementById(dstId);
    if (!dst) return;

    _pgImageSyncScrollGuard = true;
    dst.scrollLeft = src.scrollLeft;
    dst.scrollTop = src.scrollTop;
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => { _pgImageSyncScrollGuard = false; });
    } else {
      setTimeout(() => { _pgImageSyncScrollGuard = false; }, 0);
    }
  }

  function onPgImagePanMouseDown(e, cl, fileId, side) {
    if (e && e.button !== undefined && e.button !== 0) return;
    if (e && e.target && (e.target.closest("button") || e.target.closest("input") || e.target.closest("select") || e.target.closest(".pg-image-preset-group"))) {
      return;
    }
    const frameBase = document.getElementById(`pgImageFrameBase-${cl}-${fileId}`);
    const frameTarget = document.getElementById(`pgImageFrameTarget-${cl}-${fileId}`);
    if (!frameBase && !frameTarget) return;

    state._pgImagePanning = {
      cl,
      fileId,
      side,
      startX: e.clientX !== undefined ? e.clientX : 0,
      startY: e.clientY !== undefined ? e.clientY : 0,
      baseScrollLeft: frameBase ? frameBase.scrollLeft : 0,
      baseScrollTop: frameBase ? frameBase.scrollTop : 0,
      targetScrollLeft: frameTarget ? frameTarget.scrollLeft : 0,
      targetScrollTop: frameTarget ? frameTarget.scrollTop : 0,
      moved: false,
    };
    if (frameBase) frameBase.classList.add("is-panning");
    if (frameTarget) frameTarget.classList.add("is-panning");
    if (typeof document !== "undefined" && document.body) {
      document.body.classList.add("is-image-panning");
    }
    if (e && typeof e.preventDefault === "function") {
      e.preventDefault();
    }
  }

  function onPgImageDocMouseMove(e) {
    if (!state._pgImagePanning) return;
    const pan = state._pgImagePanning;
    const clientX = e.clientX !== undefined ? e.clientX : 0;
    const clientY = e.clientY !== undefined ? e.clientY : 0;
    const dx = clientX - pan.startX;
    const dy = clientY - pan.startY;
    if (!pan.moved && (Math.abs(dx) > 2 || Math.abs(dy) > 2)) {
      pan.moved = true;
    }
    const sync = isPgImageSyncPanEnabled(pan.cl, pan.fileId);
    const frameBase = document.getElementById(`pgImageFrameBase-${pan.cl}-${pan.fileId}`);
    const frameTarget = document.getElementById(`pgImageFrameTarget-${pan.cl}-${pan.fileId}`);

    if (sync) {
      if (frameBase) {
        frameBase.scrollLeft = pan.baseScrollLeft - dx;
        frameBase.scrollTop = pan.baseScrollTop - dy;
      }
      if (frameTarget) {
        frameTarget.scrollLeft = pan.targetScrollLeft - dx;
        frameTarget.scrollTop = pan.targetScrollTop - dy;
      }
    } else {
      const activeFrame = pan.side === "base" ? frameBase : frameTarget;
      if (activeFrame) {
        const origLeft = pan.side === "base" ? pan.baseScrollLeft : pan.targetScrollLeft;
        const origTop = pan.side === "base" ? pan.baseScrollTop : pan.targetScrollTop;
        activeFrame.scrollLeft = origLeft - dx;
        activeFrame.scrollTop = origTop - dy;
      }
    }
    if (e && typeof e.preventDefault === "function") e.preventDefault();
  }

  function onPgImageDocMouseUp(e) {
    if (!state._pgImagePanning) return;
    const pan = state._pgImagePanning;
    state._pgImagePanning = null;
    const frameBase = document.getElementById(`pgImageFrameBase-${pan.cl}-${pan.fileId}`);
    const frameTarget = document.getElementById(`pgImageFrameTarget-${pan.cl}-${pan.fileId}`);
    if (frameBase) frameBase.classList.remove("is-panning");
    if (frameTarget) frameTarget.classList.remove("is-panning");
    if (typeof document !== "undefined" && document.body) {
      document.body.classList.remove("is-image-panning");
    }
  }

  function initImagePanListeners() {
    if (typeof document === "undefined" || !document.addEventListener) return;
    document.addEventListener("mousemove", onPgImageDocMouseMove);
    document.addEventListener("mouseup", onPgImageDocMouseUp);
  }

  function onPgImageSwipeInput(cl, fileId, val) {
    const num = Math.max(0, Math.min(100, Number(val) || 0));
    const clip = document.getElementById(`pgImageSwipeClip-${cl}-${fileId}`);
    const divider = document.getElementById(`pgImageSwipeDivider-${cl}-${fileId}`);
    const label = document.getElementById(`pgImageSwipeVal-${cl}-${fileId}`);
    const rangeInp = document.querySelector(`#pgImageSwipe-${cl}-${fileId} .pg-image-swipe-range`);
    if (clip) {
      clip.style.clipPath = `polygon(0 0, ${num}% 0, ${num}% 100%, 0 100%)`;
    }
    if (divider) {
      divider.style.left = `${num}%`;
    }
    if (label) {
      label.textContent = `${num}%`;
    }
    if (rangeInp && String(rangeInp.value) !== String(num)) {
      rangeInp.value = String(num);
    }
  }

  function onPgImageOnionInput(cl, fileId, val) {
    const num = Math.max(0, Math.min(100, Number(val) || 0));
    const targetImg = document.getElementById(`pgImgOnionTarget-${cl}-${fileId}`);
    const label = document.getElementById(`pgImageOnionVal-${cl}-${fileId}`);
    const rangeInp = document.querySelector(`#pgImageOnion-${cl}-${fileId} .pg-image-onion-range`);
    if (targetImg) {
      targetImg.style.opacity = String(num / 100);
    }
    if (label) {
      label.textContent = `${num}%`;
    }
    if (rangeInp && String(rangeInp.value) !== String(num)) {
      rangeInp.value = String(num);
    }
  }

  function onPgImageSwipePointerDown(e, cl, fileId) {
    const stage = document.getElementById(`pgImageSwipeStage-${cl}-${fileId}`);
    if (!stage) return;
    if (typeof stage.setPointerCapture === "function" && e.pointerId) {
      try { stage.setPointerCapture(e.pointerId); } catch (_) {}
    }
    state._pgSwipeDragging = { cl, fileId, stage };
    const rect = (typeof stage.getBoundingClientRect === "function") ? stage.getBoundingClientRect() : { left: 0, width: 300 };
    if (rect && rect.width > 0) {
      const clientX = e.clientX !== undefined ? e.clientX : 150;
      const pct = Math.max(0, Math.min(100, Math.round(((clientX - rect.left) / rect.width) * 100)));
      onPgImageSwipeInput(cl, fileId, pct);
    }
    if (e && typeof e.preventDefault === "function") e.preventDefault();
  }

  function onPgImageSwipePointerMove(e, cl, fileId) {
    if (!state._pgSwipeDragging || state._pgSwipeDragging.cl !== cl || state._pgSwipeDragging.fileId !== fileId) return;
    const stage = state._pgSwipeDragging.stage || document.getElementById(`pgImageSwipeStage-${cl}-${fileId}`);
    if (!stage) return;
    const rect = (typeof stage.getBoundingClientRect === "function") ? stage.getBoundingClientRect() : { left: 0, width: 300 };
    if (rect && rect.width > 0) {
      const clientX = e.clientX !== undefined ? e.clientX : 150;
      const pct = Math.max(0, Math.min(100, Math.round(((clientX - rect.left) / rect.width) * 100)));
      onPgImageSwipeInput(cl, fileId, pct);
    }
  }

  function onPgImageSwipePointerUp(e, cl, fileId) {
    if (state._pgSwipeDragging && state._pgSwipeDragging.stage && typeof state._pgSwipeDragging.stage.releasePointerCapture === "function" && e && e.pointerId) {
      try { state._pgSwipeDragging.stage.releasePointerCapture(e.pointerId); } catch (_) {}
    }
    state._pgSwipeDragging = null;
  }

  function onPgImageOnionPointerDown(e, cl, fileId) {
    const stage = document.getElementById(`pgImageOnionStage-${cl}-${fileId}`);
    if (!stage) return;
    if (typeof stage.setPointerCapture === "function" && e.pointerId) {
      try { stage.setPointerCapture(e.pointerId); } catch (_) {}
    }
    state._pgOnionDragging = { cl, fileId, stage };
    const rect = (typeof stage.getBoundingClientRect === "function") ? stage.getBoundingClientRect() : { left: 0, width: 300 };
    if (rect && rect.width > 0) {
      const clientX = e.clientX !== undefined ? e.clientX : 150;
      const pct = Math.max(0, Math.min(100, Math.round(((clientX - rect.left) / rect.width) * 100)));
      onPgImageOnionInput(cl, fileId, pct);
    }
    if (e && typeof e.preventDefault === "function") e.preventDefault();
  }

  function onPgImageOnionPointerMove(e, cl, fileId) {
    if (!state._pgOnionDragging || state._pgOnionDragging.cl !== cl || state._pgOnionDragging.fileId !== fileId) return;
    const stage = state._pgOnionDragging.stage || document.getElementById(`pgImageOnionStage-${cl}-${fileId}`);
    if (!stage) return;
    const rect = (typeof stage.getBoundingClientRect === "function") ? stage.getBoundingClientRect() : { left: 0, width: 300 };
    if (rect && rect.width > 0) {
      const clientX = e.clientX !== undefined ? e.clientX : 150;
      const pct = Math.max(0, Math.min(100, Math.round(((clientX - rect.left) / rect.width) * 100)));
      onPgImageOnionInput(cl, fileId, pct);
    }
  }

  function onPgImageOnionPointerUp(e, cl, fileId) {
    if (state._pgOnionDragging && state._pgOnionDragging.stage && typeof state._pgOnionDragging.stage.releasePointerCapture === "function" && e && e.pointerId) {
      try { state._pgOnionDragging.stage.releasePointerCapture(e.pointerId); } catch (_) {}
    }
    state._pgOnionDragging = null;
  }

  const _pgImageBlinkIntervals = {};
  const _pgSavedOnionOpacity = {};

  function stopPgImageBlink(cl, fileId) {
    const key = `${cl}:${fileId}`;
    if (_pgImageBlinkIntervals[key]) {
      clearInterval(_pgImageBlinkIntervals[key]);
      delete _pgImageBlinkIntervals[key];
      const btn = document.getElementById(`btnImageBlink-${cl}-${fileId}`);
      if (btn) {
        btn.classList.remove("is-blinking");
        btn.innerHTML = `<span class="pg-btn-icon">⏯</span> <span>Blink</span>`;
      }
      const saved = _pgSavedOnionOpacity[key] !== undefined ? _pgSavedOnionOpacity[key] : 50;
      onPgImageOnionInput(cl, fileId, saved);
    }
  }

  function togglePgImageBlink(cl, fileId) {
    const effCl = cl || state.selectedClNumber;
    const viewer = fileId
      ? document.getElementById(`pgImageViewer-${effCl}-${fileId}`)
      : document.querySelector(".pg-image-viewer");
    if (!viewer) return;
    const actualCl = effCl || viewer.getAttribute("data-cl");
    const actualId = fileId || viewer.id.replace(`pgImageViewer-${actualCl}-`, "");
    const key = `${actualCl}:${actualId}`;
    const btn = document.getElementById(`btnImageBlink-${actualCl}-${actualId}`);

    if (_pgImageBlinkIntervals[key]) {
      stopPgImageBlink(actualCl, actualId);
      if (typeof showToast === "function") showToast("Blink comparator stopped");
    } else {
      setPgImageDiffMode(actualCl, actualId, "onion");
      const range = document.querySelector(`#pgImageOnion-${actualCl}-${actualId} .pg-image-onion-range`);
      _pgSavedOnionOpacity[key] = range ? Number(range.value) : 50;
      let flip = false;
      if (btn) {
        btn.classList.add("is-blinking");
        btn.innerHTML = `<span class="pg-btn-icon">⏹</span> <span>Blinking (1.5Hz)</span>`;
      }
      _pgImageBlinkIntervals[key] = setInterval(() => {
        flip = !flip;
        onPgImageOnionInput(actualCl, actualId, flip ? 100 : 0);
      }, 650);
      if (typeof showToast === "function") showToast("Blink comparator active · Space to stop");
    }
  }

  function flipPgImageSides(cl, fileId) {
    const effCl = cl || state.selectedClNumber;
    const viewer = fileId
      ? document.getElementById(`pgImageViewer-${effCl}-${fileId}`)
      : document.querySelector(".pg-image-viewer");
    if (!viewer) return;
    const actualCl = effCl || viewer.getAttribute("data-cl");
    const actualId = fileId || viewer.id.replace(`pgImageViewer-${actualCl}-`, "");

    stopPgImageBlink(actualCl, actualId);

    const vp = document.getElementById(`pgImageViewport-${actualCl}-${actualId}`);
    const isSwipe = vp && vp.classList.contains("mode-swipe");
    if (isSwipe) {
      const range = document.querySelector(`#pgImageSwipe-${actualCl}-${actualId} .pg-image-swipe-range`);
      const cur = range ? Number(range.value) : 50;
      const next = cur < 50 ? 100 : 0;
      onPgImageSwipeInput(actualCl, actualId, next);
      if (typeof showToast === "function") {
        showToast(next === 0 ? "Showing Base (0%)" : "Showing Patchset (100%)");
      }
    } else {
      setPgImageDiffMode(actualCl, actualId, "onion");
      const range = document.querySelector(`#pgImageOnion-${actualCl}-${actualId} .pg-image-onion-range`);
      const cur = range ? Number(range.value) : 50;
      const next = cur < 50 ? 100 : 0;
      onPgImageOnionInput(actualCl, actualId, next);
      if (typeof showToast === "function") {
        showToast(next === 0 ? "Showing Base (0%)" : "Showing Patchset (100%)");
      }
    }
  }

  function onPgImageLoaded(cl, fileId, side) {
    const baseImg = document.getElementById(`pgImgBase-${cl}-${fileId}`);
    const targetImg = document.getElementById(`pgImgTarget-${cl}-${fileId}`);
    const baseDimEl = document.getElementById(`pgImageBaseDim-${cl}-${fileId}`);
    const targetDimEl = document.getElementById(`pgImageTargetDim-${cl}-${fileId}`);
    const pillEl = document.getElementById(`pgImageDimPill-${cl}-${fileId}`);

    let baseW = 0, baseH = 0, targetW = 0, targetH = 0;
    if (baseImg && baseImg.naturalWidth) {
      baseW = baseImg.naturalWidth;
      baseH = baseImg.naturalHeight;
      if (baseDimEl) baseDimEl.textContent = `${baseW} × ${baseH}`;
    }
    if (targetImg && targetImg.naturalWidth) {
      targetW = targetImg.naturalWidth;
      targetH = targetImg.naturalHeight;
      if (targetDimEl) targetDimEl.textContent = `${targetW} × ${targetH}`;
    }

    if (pillEl) {
      if (baseW && targetW) {
        if (baseW === targetW && baseH === targetH) {
          pillEl.innerHTML = `<span class="dim-equal">✓ ${baseW} × ${baseH}</span> <span style="color:var(--g-muted);">(Identical)</span>`;
        } else {
          const dw = targetW - baseW;
          const dh = targetH - baseH;
          const signW = dw > 0 ? `+${dw}` : String(dw);
          const signH = dh > 0 ? `+${dh}` : String(dh);
          pillEl.innerHTML = `<span class="dim-diff">${baseW}×${baseH} → ${targetW}×${targetH}</span> <span style="color:var(--g-muted);">(${signW}w, ${signH}h)</span>`;
        }
      } else if (targetW) {
        pillEl.textContent = `${targetW} × ${targetH} (Added)`;
      } else if (baseW) {
        pillEl.textContent = `${baseW} × ${baseH} (Deleted)`;
      }
    }
  }

  function onPgImageError(cl, fileId, side) {
    const dimEl = side === "base"
      ? document.getElementById(`pgImageBaseDim-${cl}-${fileId}`)
      : document.getElementById(`pgImageTargetDim-${cl}-${fileId}`);
    if (dimEl) dimEl.textContent = "Error loading";
  }

  function cyclePgImageDiffMode(cl, fileId) {
    const viewer = fileId
      ? document.getElementById(`pgImageViewer-${cl}-${fileId}`)
      : document.querySelector(".pg-image-viewer");
    if (!viewer) return;
    const effCl = cl || viewer.getAttribute("data-cl");
    const effId = fileId || (viewer.id.replace(`pgImageViewer-${effCl}-`, ""));
    const viewport = document.getElementById(`pgImageViewport-${effCl}-${effId}`);
    if (!viewport) return;

    let curMode = "2-up";
    if (viewport.classList.contains("mode-swipe")) curMode = "swipe";
    else if (viewport.classList.contains("mode-onion")) curMode = "onion";

    const nextMode = curMode === "2-up" ? "swipe" : (curMode === "swipe" ? "onion" : "2-up");
    setPgImageDiffMode(effCl, effId, nextMode);
    if (typeof showToast === "function") {
      const modeNames = { "2-up": "2-up (Side-by-side)", "swipe": "Swipe comparison", "onion": "Onion skin" };
      showToast(`Image mode: ${modeNames[nextMode] || nextMode}`);
    }
  }

  const _pgImageCache = {};

  async function loadPgImageData(cl, ps, file, fileId, status) {
    const cacheKeyTarget = `${cl}:${ps}:${file}:target`;
    const cacheKeyBase = `${cl}:${ps}:${file}:base`;
    const isAdded = status === "A";
    const isDeleted = status === "D";

    let targetDataUrl = _pgImageCache[cacheKeyTarget] || "";
    let baseDataUrl = _pgImageCache[cacheKeyBase] || "";

    function generatePlaceholderSvg(label, color, sublabel) {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200">
        <rect width="320" height="200" fill="${color}" rx="6"/>
        <text x="160" y="95" fill="#ffffff" font-family="sans-serif" font-size="16" font-weight="bold" text-anchor="middle">${label}</text>
        <text x="160" y="125" fill="rgba(255,255,255,0.8)" font-family="sans-serif" font-size="12" text-anchor="middle">${sublabel}</text>
      </svg>`;
      try {
        return `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`;
      } catch (_e) {
        return "";
      }
    }

    async function fetchPgImageDataUrl(url) {
      if (typeof fetch !== "function" || typeof sidecarFetch !== "function") {
        return { dataUrl: "", error: "" };
      }
      try {
        const resp = await sidecarFetch(url);
        if (!resp || !resp.ok) {
          return { dataUrl: "", error: resp ? `HTTP ${resp.status}` : "no response" };
        }
        const json = await resp.json();
        return { dataUrl: (json && json.data_url) || "", error: (json && json.error) || "" };
      } catch (err) {
        return { dataUrl: "", error: String((err && err.message) || err) };
      }
    }

    let fetchErrReason = "";
    if (!isDeleted && !targetDataUrl) {
      const targetRes = await fetchPgImageDataUrl(
        `/api/gerrit/file-content?cl=${cl}&file=${encodeURIComponent(file)}&ps=${ps}`
      );
      if (targetRes.dataUrl) {
        targetDataUrl = targetRes.dataUrl;
        _pgImageCache[cacheKeyTarget] = targetDataUrl;
      } else if (targetRes.error) {
        fetchErrReason = targetRes.error;
      }
    }
    if (!isAdded && !baseDataUrl) {
      const baseRes = await fetchPgImageDataUrl(
        `/api/gerrit/file-content?cl=${cl}&file=${encodeURIComponent(file)}&ps=${ps}&parent=1`
      );
      if (baseRes.dataUrl) {
        baseDataUrl = baseRes.dataUrl;
        _pgImageCache[cacheKeyBase] = baseDataUrl;
      } else if (baseRes.error && !fetchErrReason) {
        fetchErrReason = baseRes.error;
      }
    }
    if (fetchErrReason) {
      console.warn(
        `Failed to load image diff '${file}' for ag/${cl} (PS ${ps}): ${fetchErrReason}. Click 'Reload Images' or check 'gcert'.`
      );
    }

    if (!targetDataUrl && !isDeleted) {
      targetDataUrl = generatePlaceholderSvg(
        fetchErrReason ? `Load Failed (${escapeHtml(fetchErrReason)})` : "New Patchset",
        "#1b5e20",
        escapeHtml(file)
      );
    }
    if (!baseDataUrl && !isAdded) {
      baseDataUrl = generatePlaceholderSvg(
        fetchErrReason ? `Load Failed (${escapeHtml(fetchErrReason)})` : "Base Version",
        "#b71c1c",
        escapeHtml(file)
      );
    }

    const setSrc = (id, src) => {
      const el = document.getElementById(id);
      if (el && src) el.src = src;
    };

    if (baseDataUrl) {
      setSrc(`pgImgBase-${cl}-${fileId}`, baseDataUrl);
      setSrc(`pgImgSwipeBase-${cl}-${fileId}`, baseDataUrl);
      setSrc(`pgImgOnionBase-${cl}-${fileId}`, baseDataUrl);
    }
    if (targetDataUrl) {
      setSrc(`pgImgTarget-${cl}-${fileId}`, targetDataUrl);
      setSrc(`pgImgSwipeTarget-${cl}-${fileId}`, targetDataUrl);
      setSrc(`pgImgOnionTarget-${cl}-${fileId}`, targetDataUrl);
    }
  }

  function reloadPgImageData(cl, ps, file, fileId, status) {
    delete _pgImageCache[`${cl}:${ps}:${file}:target`];
    delete _pgImageCache[`${cl}:${ps}:${file}:base`];
    loadPgImageData(cl, ps, file, fileId, status);
  }

  async function downloadPgBinaryFile(cl, file, ps, isParent = false) {
    const parentParam = isParent ? "&parent=1" : "";
    const url = `/api/gerrit/file-content?cl=${cl}&file=${encodeURIComponent(file)}&ps=${ps}${parentParam}`;
    try {
      if (typeof fetch !== "function" || typeof sidecarFetch !== "function") return;
      const resp = await sidecarFetch(url);
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        const reason = errData.error || `HTTP ${resp.status}`;
        if (typeof showToast === "function") {
          showToast(
            `Failed to download '${file}' from ag/${cl} (PS ${ps}): ${reason}. Check 'gcert' or reopen the sidecar URL and retry.`
          );
        }
        return;
      }
      const data = await resp.json();
      if (data && (data.status === "error" || data.error)) {
        if (typeof showToast === "function") {
          showToast(
            `Failed to download '${file}' from ag/${cl} (PS ${ps}): ${data.error || "Unknown error"}. Check 'gcert' and retry.`
          );
        }
        return;
      }
      const b64 = data.b64_content || "";
      const filename = file.split("/").pop() || "download.bin";
      const mime = data.mime_type || "application/octet-stream";
      const dataUri = data.data_url || (b64 ? `data:${mime};base64,${b64}` : "");

      if (dataUri && typeof document !== "undefined") {
        const a = document.createElement("a");
        a.href = dataUri;
        a.download = (isParent ? "base_" : "") + filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        if (typeof showToast === "function") showToast(`Downloaded ${(isParent ? "base " : "") + filename}`);
      } else if (data.content && typeof document !== "undefined") {
        const blob = new Blob([data.content], { type: "text/plain" });
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = blobUrl;
        a.download = (isParent ? "base_" : "") + filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(blobUrl);
        if (typeof showToast === "function") showToast(`Downloaded ${(isParent ? "base " : "") + filename}`);
      } else if (typeof showToast === "function") {
        showToast(
          `Failed to download '${file}' from ag/${cl} (PS ${ps}): server returned empty file content. Run 'gcert' and retry or download from Gerrit.`
        );
      }
    } catch (e) {
      if (typeof showToast === "function") {
        showToast(
          `Failed to download '${file}' from ag/${cl} (PS ${ps}): ${(e && e.message) || e}. Check sidecar status and retry.`
        );
      }
    }
  }

  function buildPgDiffOverviewMarkers(lines = [], fileComments = []) {
    if (!Array.isArray(lines) || lines.length === 0) {
      return { markers: [], totalLines: 0, addCount: 0, delCount: 0, commentCount: 0, unresolvedCount: 0 };
    }
    const total = lines.length;
    const markers = [];
    let addCount = 0;
    let delCount = 0;

    let curType = null;
    let chunkStartIdx = 0;
    let chunkCount = 0;
    let chunkFirstLine = 1;

    function pushChunkMarker() {
      const topPct = (chunkStartIdx / total) * 100;
      const heightPct = Math.max((chunkCount / total) * 100, 1.2);
      const isDel = curType === "del";
      markers.push({
        type: curType,
        top: topPct,
        height: heightPct,
        line: chunkFirstLine,
        count: chunkCount,
        isBase: isDel,
        _prefixHtml: `<div class="pg-diff-ov-marker is-${curType}" style="top:${topPct.toFixed(2)}%;height:${heightPct.toFixed(2)}%;" title="Line ${chunkFirstLine} (${isDel ? "-" : "+"}${chunkCount} lines) · Click to jump" onclick="event.stopPropagation(); scrollToPgCommentOrFile(`,
        _suffixHtml: isDel ? `, ${chunkFirstLine}, null, true)"></div>` : `, ${chunkFirstLine})"></div>`,
      });
    }

    for (let i = 0; i < total; i++) {
      const ln = lines[i];
      const type = ln ? ln.type : null;
      if (type === "add" || type === "del") {
        if (type === "add") addCount++;
        if (type === "del") delCount++;
        if (curType === type) {
          chunkCount++;
        } else {
          if (curType) {
            pushChunkMarker();
          }
          curType = type;
          chunkStartIdx = i;
          chunkCount = 1;
          chunkFirstLine = ln.new || ln.old || 1;
        }
      } else {
        if (curType) {
          pushChunkMarker();
          curType = null;
          chunkCount = 0;
        }
      }
    }
    if (curType) {
      pushChunkMarker();
    }

    let commentCount = 0;
    let unresolvedCount = 0;
    if (Array.isArray(fileComments)) {
      fileComments.forEach(c => {
        if (!c) return;
        commentCount++;
        const isUnresolved = Boolean(c.unresolved);
        if (isUnresolved) unresolvedCount++;
        const targetLn = c.line || c.start_line || 1;
        const isBase = String(c.side || "REVISION").toUpperCase() === "PARENT";

        let matchIdx = lines.findIndex(ln => (isBase ? ln.old === targetLn : ln.new === targetLn));
        if (matchIdx === -1) {
          matchIdx = lines.findIndex(ln => ln.new === targetLn || ln.old === targetLn);
        }
        if (matchIdx === -1) {
          matchIdx = Math.min(Math.max(0, targetLn - 1), total - 1);
        }
        const topPct = (matchIdx / total) * 100;
        const author = c.author || c.displayAuthor || (c.author_email ? c.author_email.split("@")[0] : "Reviewer");
        markers.push({
          type: isUnresolved ? "unresolved" : "comment",
          top: topPct,
          height: 1.8,
          line: targetLn,
          commentId: c.id || null,
          author: author,
          isUnresolved: isUnresolved,
          isBase: isBase,
          count: 1,
        });
      });
    }

    let joinedParts = null;
    if (commentCount === 0) {
      const mLen = markers.length;
      if (mLen === 0) {
        joinedParts = [""];
      } else {
        joinedParts = new Array(mLen + 1);
        joinedParts[0] = markers[0]._prefixHtml;
        for (let i = 0; i < mLen - 1; i++) {
          joinedParts[i + 1] = markers[i]._suffixHtml + markers[i + 1]._prefixHtml;
        }
        joinedParts[mLen] = markers[mLen - 1]._suffixHtml;
      }
    }

    return { markers, _joinedParts: joinedParts, totalLines: total, addCount, delCount, commentCount, unresolvedCount };
  }

  function renderPgDiffOverviewBarHtml(f, fallbackCl, safeFile, fileComments = [], preLinesSig = "", preCommentsSig = "") {
    const rawLines = Array.isArray(f.lines) ? f.lines : [];
    if (rawLines.length === 0) return "";
    const canMemoBar = (
      typeof window === "undefined" ||
      !window.buildPgDiffOverviewMarkers ||
      window.buildPgDiffOverviewMarkers === buildPgDiffOverviewMarkers
    );
    const commentsSig = canMemoBar
      ? (preCommentsSig || computePgFileCommentsCacheSig(fallbackCl, fileComments))
      : "";
    const linesSig = preLinesSig || getPgFileLinesVersionSig(f);
    if (canMemoBar) {
      const ovSub = f._ovBarSub;
      if (
        ovSub &&
        ovSub.cl === fallbackCl &&
        ovSub.safeFile === safeFile &&
        ovSub.linesSig === linesSig &&
        ovSub.commentsSig === commentsSig &&
        f._cachedOverviewBarKey === ovSub.key &&
        typeof f._cachedOverviewBarHtml === "string"
      ) {
        return f._cachedOverviewBarHtml;
      }
    }
    const barSig = canMemoBar ? `${fallbackCl}:${safeFile}:${linesSig}:${commentsSig}` : "";
    if (barSig && f._cachedOverviewBarKey === barSig && typeof f._cachedOverviewBarHtml === "string") {
      f._ovBarSub = { cl: fallbackCl, safeFile, linesSig, commentsSig, key: barSig };
      return f._cachedOverviewBarHtml;
    }
    const hasNoComments = !Array.isArray(fileComments) || fileComments.length === 0;
    const info = (canMemoBar && hasNoComments && f._cachedBaseOverviewSig === linesSig && f._cachedBaseOverviewMarkers)
      ? f._cachedBaseOverviewMarkers
      : buildPgDiffOverviewMarkers(rawLines, fileComments);
    const safeFileId = (f._cachedFilePath === f.file && f._cachedSafeFileId)
      ? f._cachedSafeFileId
      : escapeHtml(String(f.file).replace(/[^a-zA-Z0-9_-]/g, "_"));

    const clickTarget = `${fallbackCl}, '${safeFile}'`;
    let markersHtml = "";
    if (info._joinedParts && info.commentCount === 0) {
      markersHtml = info._joinedParts.length === 1
        ? info._joinedParts[0]
        : info._joinedParts.join(clickTarget);
    } else {
      const mList = info.markers;
      for (let i = 0; i < mList.length; i++) {
        const m = mList[i];
        if (m._prefixHtml && m._suffixHtml) {
          markersHtml += m._prefixHtml + clickTarget + m._suffixHtml;
          continue;
        }
        const topStyle = `top:${m.top.toFixed(2)}%;`;
        const heightStyle = m.height ? `height:${m.height.toFixed(2)}%;` : "";
        const cls = `pg-diff-ov-marker is-${m.type}`;
        let title = "";
        let clickHandler = "";

        if (m.type === "add") {
          title = `Line ${m.line} (+${m.count} lines) · Click to jump`;
          clickHandler = `event.stopPropagation(); scrollToPgCommentOrFile(${fallbackCl}, '${safeFile}', ${m.line})`;
        } else if (m.type === "del") {
          title = `Line ${m.line} (-${m.count} lines) · Click to jump`;
          clickHandler = `event.stopPropagation(); scrollToPgCommentOrFile(${fallbackCl}, '${safeFile}', ${m.line}, null, true)`;
        } else if (m.type === "unresolved") {
          title = `Line ${m.line} (Unresolved thread by ${escapeHtml(m.author)}) · Click to jump`;
          clickHandler = `event.stopPropagation(); scrollToPgCommentOrFile(${fallbackCl}, '${safeFile}', ${m.line}, '${m.commentId || ""}', ${m.isBase ? "true" : "false"})`;
        } else {
          title = `Line ${m.line} (Comment by ${escapeHtml(m.author)}) · Click to jump`;
          clickHandler = `event.stopPropagation(); scrollToPgCommentOrFile(${fallbackCl}, '${safeFile}', ${m.line}, '${m.commentId || ""}', ${m.isBase ? "true" : "false"})`;
        }

        markersHtml += `<div class="${cls}" style="${topStyle}${heightStyle}" title="${title}" onclick="${clickHandler}"></div>`;
      }
    }

    const outHtml = `<div class="pg-diff-overview-bar" id="pgDiffOverviewBar-${fallbackCl}-${safeFileId}" data-cl="${fallbackCl}" data-file="${safeFile}" title="Diff minimap overview · Click or drag to jump" role="region" aria-label="Diff minimap for ${safeFile}">
      <div class="pg-diff-ov-track" onclick="onPgDiffOverviewTrackClick(event, ${fallbackCl}, '${safeFile}')" onmousedown="onPgDiffOverviewTrackMouseDown(event, ${fallbackCl}, '${safeFile}')">
        ${markersHtml}
        <div class="pg-diff-ov-thumb" id="pgDiffOvThumb-${fallbackCl}-${safeFileId}"></div>
      </div>
    </div>`;
    if (barSig) {
      f._cachedOverviewBarKey = barSig;
      f._cachedOverviewBarHtml = outHtml;
      f._ovBarSub = { cl: fallbackCl, safeFile, linesSig, commentsSig, key: barSig };
    }
    return outHtml;
  }

  function onPgDiffOverviewTrackClick(event, clNum, filePath) {
    const track = event.currentTarget;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    if (rect.height <= 0) return;
    const ratio = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    jumpPgDiffOverviewRatio(clNum, filePath, ratio);
  }

  function onPgDiffOverviewTrackMouseDown(event, clNum, filePath) {
    if (event.button !== 0) return;
    const track = event.currentTarget;
    if (!track) return;

    const updateFromPointer = (clientY) => {
      const rect = track.getBoundingClientRect();
      if (rect.height <= 0) return;
      const ratio = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
      jumpPgDiffOverviewRatio(clNum, filePath, ratio);
    };

    updateFromPointer(event.clientY);

    const onMouseMove = (moveEv) => {
      updateFromPointer(moveEv.clientY);
    };
    const onMouseUp = () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      }
    };
    if (typeof window !== "undefined") {
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    }
  }

  function jumpPgDiffOverviewRatio(clNum, filePath, ratio) {
    const s = (typeof window !== "undefined" && window.state) ? window.state : ((typeof state !== "undefined" && state) ? state : {});
    const num = Number(clNum || s.selectedClNumber || (typeof state !== "undefined" && state.selectedClNumber) || 0);
    if (!num || !filePath) return null;
    const bundle = (s.diffsByCl && s.diffsByCl[num]) || (typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[num]);
    const files = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    const fileObj = files.find(item => item && item.file === filePath);
    const rawLines = fileObj && Array.isArray(fileObj.lines) ? fileObj.lines : [];
    let targetLine = 1;
    let isBase = false;
    if (rawLines.length > 0) {
      const targetIdx = Math.max(0, Math.min(rawLines.length - 1, Math.floor(ratio * (rawLines.length - 1))));
      const lineObj = rawLines[targetIdx];
      targetLine = lineObj ? (lineObj.new || lineObj.old || 1) : 1;
      isBase = lineObj ? (!lineObj.new && !!lineObj.old) : false;
    }
    const scrollFn = (typeof scrollToPgCommentOrFile === "function")
      ? scrollToPgCommentOrFile
      : (typeof window !== "undefined" && typeof window.scrollToPgCommentOrFile === "function" ? window.scrollToPgCommentOrFile : null);
    if (scrollFn) {
      scrollFn(num, filePath, targetLine, null, isBase);
    }
    return { clNum: num, filePath, targetLine, isBase, ratio };
  }

  function syncPgDiffOverviewThumbs() {
    if (typeof document === "undefined") return;
    const diffScroll = document.getElementById("diffScroll") || document.getElementById("diffPane");
    if (!diffScroll) return;
    const scrollRect = diffScroll.getBoundingClientRect();
    const bars = document.querySelectorAll(".pg-diff-overview-bar");
    if (!bars || !bars.forEach) return;
    // Phase 1: Batch all DOM geometry reads before mutating thumb styles.
    const thumbUpdates = [];
    bars.forEach(bar => {
      const fileBlock = bar.closest ? bar.closest(".pg-file-diff-block") : null;
      if (!fileBlock) return;
      const codeTable = fileBlock.querySelector ? fileBlock.querySelector(".code-table") : null;
      if (!codeTable) return;
      const thumb = bar.querySelector ? bar.querySelector(".pg-diff-ov-thumb") : null;
      if (!thumb) return;
      const tableRect = codeTable.getBoundingClientRect();
      if (tableRect.height <= 0) return;
      const visibleTop = Math.max(0, scrollRect.top - tableRect.top);
      const visibleBottom = Math.min(tableRect.height, scrollRect.bottom - tableRect.top);
      if (visibleBottom <= 0 || visibleTop >= tableRect.height) {
        thumbUpdates.push({ thumb, nextTop: null, nextHeight: "0%" });
        return;
      }
      const topPct = (visibleTop / tableRect.height) * 100;
      const heightPct = Math.max(4, ((visibleBottom - visibleTop) / tableRect.height) * 100);
      thumbUpdates.push({
        thumb,
        nextTop: topPct.toFixed(2) + "%",
        nextHeight: heightPct.toFixed(2) + "%",
      });
    });
    // Phase 2: Apply thumb style updates without interleaved layout reads.
    thumbUpdates.forEach(item => {
      if (item.nextTop !== null && item.thumb.style.top !== item.nextTop) {
        item.thumb.style.top = item.nextTop;
      }
      if (item.thumb.style.height !== item.nextHeight) {
        item.thumb.style.height = item.nextHeight;
      }
    });
  }

  function extractPgFileDiffSymbols(f) {
    if (!f || f.file === "/COMMIT_MSG" || f.file === "/MERGE_LIST") return [];
    if (Array.isArray(f.symbols) && f.symbols.length > 0) {
      return f.symbols;
    }
    const lines = Array.isArray(f.lines) ? f.lines : [];
    const symbols = [];
    const seen = new Set();
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line) continue;
      const text = String(line.text || "");
      let sym = "";
      let lineNum = 0;
      if (line.type === "hunk") {
        const m = text.match(/^@@[^@]+@@\s*(.+?)\s*$/);
        if (m) {
          sym = (typeof extractSymbolFromCodeLine === "function")
            ? extractSymbolFromCodeLine(m[1])
            : "";
          lineNum = Number((lines[i + 1] && (lines[i + 1].new || lines[i + 1].old)) || 1);
        }
      } else if (line.type === "add" || line.type === "ctx") {
        sym = extractSymbolFromCodeLine(text, true);
        if (sym) {
          lineNum = Number(line.new || line.old || 1);
        }
      }
      if (sym && !seen.has(sym)) {
        seen.add(sym);
        symbols.push({
          name: sym,
          line: lineNum,
          kind: (sym.startsWith("class ") || sym.startsWith("struct ")) ? "class" : "function",
        });
      }
    }
    return symbols;
  }

  function renderPgFileOutlineButtonHtml(clNum, filePath, symbols = []) {
    if (!Array.isArray(symbols) || symbols.length === 0) return "";
    const safeFile = escapeHtml(String(filePath || ""));
    const safeFileId = escapeHtml(String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_"));
    const count = symbols.length;
    const isMenuOpen = Boolean(
      state.activeFileOutlineMenu &&
      Number(state.activeFileOutlineMenu.cl) === Number(clNum) &&
      state.activeFileOutlineMenu.file === filePath
    );
    const dropdownHtml = renderPgFileOutlineDropdownHtml(clNum, filePath, symbols);

    return `<div class="pg-file-outline-wrap" id="pgFileOutlineWrap-${clNum}-${safeFileId}">
      <button
        type="button"
        class="pg-btn-file-outline ${isMenuOpen ? "is-active" : ""}"
        id="btnPgFileOutline-${clNum}-${safeFileId}"
        onclick="event.stopPropagation(); togglePgFileOutlineMenu(${clNum}, '${escapeJsQuote(filePath)}')"
        title="Jump to modified symbol in ${safeFile} (Outline: ${count} symbol${count === 1 ? "" : "s"})"
        aria-haspopup="true"
        aria-expanded="${isMenuOpen}"
      >
        <span class="pg-outline-icon" aria-hidden="true">🔣</span>
        <span class="pg-outline-btn-text">Symbols (${count})</span>
        <span class="pg-outline-caret" aria-hidden="true">▾</span>
      </button>
      ${dropdownHtml}
    </div>`;
  }

  function renderPgFileOutlineDropdownHtml(clNum, filePath, symbols = []) {
    if (!Array.isArray(symbols) || symbols.length === 0) return "";
    const safeFile = escapeHtml(String(filePath || ""));
    const safeFileId = escapeHtml(String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_"));
    const isMenuOpen = Boolean(
      state.activeFileOutlineMenu &&
      Number(state.activeFileOutlineMenu.cl) === Number(clNum) &&
      state.activeFileOutlineMenu.file === filePath
    );
    const count = symbols.length;
    const filterInputHtml = count >= 4
      ? `<div class="pg-outline-filter-wrap">
          <input
            type="text"
            class="pg-outline-filter-input"
            id="pgOutlineFilterInput-${clNum}-${safeFileId}"
            placeholder="Filter symbols..."
            oninput="onPgFileOutlineFilterInput(event, ${clNum}, '${safeFileId}')"
            onclick="event.stopPropagation()"
            autocomplete="off"
            spellcheck="false"
            aria-label="Filter symbols in ${safeFile}"
          />
        </div>`
      : "";

    const itemsHtml = symbols.map(s => {
      const symName = String(s.name || "");
      const safeSymName = escapeHtml(symName);
      const lineNum = Number(s.line || s.new_line || 1);
      const isClass = s.kind === "class" || symName.startsWith("class ") || symName.startsWith("struct ");
      const iconLetter = isClass ? "C" : "ƒ";
      const iconCls = isClass ? "is-class" : "is-fn";
      const deltaBadge = (s.additions || s.deletions)
        ? `<span class="pg-outline-delta">+${s.additions || 0}/-${s.deletions || 0}</span>`
        : "";
      return `<button
        type="button"
        class="pg-outline-item"
        data-symbol-name="${safeSymName.toLowerCase()}"
        onclick="event.stopPropagation(); jumpToPgFileSymbol(${clNum}, '${escapeJsQuote(filePath)}', ${lineNum}, '${escapeJsQuote(symName)}')"
        title="${isClass ? 'Class' : 'Function'}: ${safeSymName} (line ${lineNum})"
      >
        <span class="pg-outline-item-icon ${iconCls}" aria-hidden="true">${iconLetter}</span>
        <span class="pg-outline-item-name">${safeSymName}</span>
        <span class="pg-outline-item-line">:${lineNum}</span>
        ${deltaBadge}
      </button>`;
    }).join("");

    return `<div
      class="pg-file-outline-dropdown"
      id="pgFileOutlineDropdown-${clNum}-${safeFileId}"
      onclick="event.stopPropagation()"
      style="${isMenuOpen ? "display:flex;" : "display:none;"}"
    >
      <div class="pg-outline-header">
        <span>Symbols in diff (${count})</span>
        <button type="button" class="pg-outline-close-btn" onclick="closeAllPgFileOutlineMenus()" title="Close symbols outline (Esc)">✕</button>
      </div>
      ${filterInputHtml}
      <div class="pg-outline-list" id="pgOutlineList-${clNum}-${safeFileId}">
        ${itemsHtml}
      </div>
    </div>`;
  }

  function togglePgFileOutlineMenu(clNum, filePath) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const targetFile = String(filePath || "");
    const isOpen = Boolean(
      state.activeFileOutlineMenu &&
      Number(state.activeFileOutlineMenu.cl) === num &&
      state.activeFileOutlineMenu.file === targetFile
    );
    closeAllPgFileOutlineMenus();
    if (!isOpen && targetFile) {
      state.activeFileOutlineMenu = { cl: num, file: targetFile };
      const safeFileId = escapeHtml(targetFile.replace(/[^a-zA-Z0-9_-]/g, "_"));
      const dropdown = document.getElementById(`pgFileOutlineDropdown-${num}-${safeFileId}`);
      const btn = document.getElementById(`btnPgFileOutline-${num}-${safeFileId}`);
      if (dropdown) {
        dropdown.style.display = "flex";
      }
      if (btn) {
        btn.classList.add("is-active");
        btn.setAttribute("aria-expanded", "true");
      }
      const filterInput = document.getElementById(`pgOutlineFilterInput-${num}-${safeFileId}`);
      if (filterInput) {
        filterInput.value = "";
        setTimeout(() => filterInput.focus(), 50);
      }
    }
  }

  function closeAllPgFileOutlineMenus() {
    state.activeFileOutlineMenu = null;
    if (typeof document === "undefined") return;
    const dropdowns = document.querySelectorAll(".pg-file-outline-dropdown");
    dropdowns.forEach(dd => {
      dd.style.display = "none";
    });
    const btns = document.querySelectorAll(".pg-btn-file-outline");
    btns.forEach(btn => {
      btn.classList.remove("is-active");
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function onPgFileOutlineFilterInput(event, clNum, safeFileId) {
    const input = event && event.target;
    if (!input) return;
    const query = String(input.value || "").trim().toLowerCase();
    const list = document.getElementById(`pgOutlineList-${clNum}-${safeFileId}`);
    if (!list) return;
    const items = list.querySelectorAll(".pg-outline-item");
    let matchCount = 0;
    items.forEach(item => {
      const sym = (item.getAttribute("data-symbol-name") || "").toLowerCase();
      if (!query || sym.includes(query)) {
        item.style.display = "flex";
        matchCount++;
      } else {
        item.style.display = "none";
      }
    });
    let emptyEl = list.querySelector(".pg-outline-empty");
    if (matchCount === 0) {
      if (!emptyEl) {
        emptyEl = document.createElement("div");
        emptyEl.className = "pg-outline-empty";
        emptyEl.style.cssText = "padding:8px 10px;font-size:11px;color:var(--g-muted,#9aa0a6);text-align:center;";
        emptyEl.textContent = `No symbols match "${query}"`;
        list.appendChild(emptyEl);
      } else {
        emptyEl.textContent = `No symbols match "${query}"`;
        emptyEl.style.display = "block";
      }
    } else if (emptyEl) {
      emptyEl.style.display = "none";
    }
  }

  function jumpToPgFileSymbol(clNum, filePath, lineNum, symbolName) {
    closeAllPgFileOutlineMenus();
    const num = Number(clNum || state.selectedClNumber || 0);
    const safeFile = String(filePath || "");
    const safeFileId = escapeHtml(safeFile.replace(/[^a-zA-Z0-9_-]/g, "_"));
    const fileBlock = document.getElementById(`file-diff-${num}-${safeFileId}`);
    if (!fileBlock) {
      if (typeof showToast === "function") {
        showToast(`Cannot locate diff block for ${safeFile}`);
      }
      return;
    }
    const ps = Number(fileBlock.getAttribute("data-ps") || 1);
    if (fileBlock.classList.contains("is-collapsed") && typeof setPgFileCollapsed === "function") {
      setPgFileCollapsed(num, ps, safeFile, false);
    }
    const lineStr = String(lineNum || 1);
    let targetEl = fileBlock.querySelector(`.code-line[data-new-ln="${lineStr}"]`) ||
                   fileBlock.querySelector(`.code-line[data-old-ln="${lineStr}"]`) ||
                   fileBlock.querySelector(`.line-num[data-new-line="${lineStr}"]`) ||
                   fileBlock.querySelector(`.code-line[data-line="${lineStr}"]`);
    if (!targetEl) {
      const lines = fileBlock.querySelectorAll(".code-line");
      for (const ln of lines) {
        const n = Number(ln.getAttribute("data-new-ln") || ln.getAttribute("data-old-ln") || 0);
        if (n >= Number(lineNum)) {
          targetEl = ln;
          break;
        }
      }
    }
    if (!targetEl) {
      targetEl = fileBlock;
    }
    if (targetEl.scrollIntoView) {
      targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    targetEl.classList.add("pg-symbol-jump-highlight");
    setTimeout(() => {
      targetEl.classList.remove("pg-symbol-jump-highlight");
    }, 2000);
    if (typeof showToast === "function") {
      showToast(`Jumped to ${symbolName} (L${lineNum}) ✓`, 2000);
    }
  }

  /* PolyGerrit Rendered Markdown File Viewer (<gr-markdown-viewer>) */
  const PG_MARKDOWN_EXTENSIONS = new Set(["md", "markdown", "mdx", "mdown", "mkd"]);
  if (typeof window !== "undefined") {
    window.PG_MARKDOWN_EXTENSIONS = PG_MARKDOWN_EXTENSIONS;
  }

  function isPgMarkdownFile(filePath) {
    return PG_MARKDOWN_EXTENSIONS.has(getPgCleanFileExtension(filePath));
  }

  function getPgMarkdownViewMode(clNum, filePath) {
    if (!state.markdownViewModeByFile) state.markdownViewModeByFile = {};
    const num = Number(clNum || state.selectedClNumber || 0);
    const key = `${num}:${String(filePath || "")}`;
    return state.markdownViewModeByFile[key] || "preview";
  }

  function getPgMarkdownPreviewSide(clNum, filePath) {
    if (!state.markdownPreviewSideByFile) state.markdownPreviewSideByFile = {};
    const num = Number(clNum || state.selectedClNumber || 0);
    const key = `${num}:${String(filePath || "")}`;
    return state.markdownPreviewSideByFile[key] || "new";
  }

  function stripPgDiffLinePrefix(rawText, lineType) {
    const text = String(rawText == null ? "" : rawText);
    if (lineType === "add") {
      return text.startsWith("+") ? text.slice(1) : text;
    }
    if (lineType === "del") {
      return text.startsWith("-") ? text.slice(1) : text;
    }
    if (lineType === "ctx") {
      return text.startsWith(" ") ? text.slice(1) : text;
    }
    return text;
  }

  function extractPgFileSourceFromDiff(f, clNum, ps, side = "new") {
    const fileObj = f || {};
    const filePath = String(fileObj.file || "");
    const num = Number(clNum || state.selectedClNumber || 0);
    const patchSet = Number(ps || (state.diffsByCl && state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    const cacheKey = `${num}:${patchSet}:${filePath}`;

    if (side === "new") {
      if (typeof fileObj.raw_content === "string") {
        const lines = fileObj.raw_content.split("\n");
        return { text: fileObj.raw_content, lineCount: lines.length, isPartial: false, hasOldLines: false, hasNewLines: true };
      }
      if (typeof fileObj.content === "string") {
        const lines = fileObj.content.split("\n");
        return { text: fileObj.content, lineCount: lines.length, isPartial: false, hasOldLines: false, hasNewLines: true };
      }
      if (state.fileContentCache && Array.isArray(state.fileContentCache[cacheKey])) {
        const cachedLines = state.fileContentCache[cacheKey];
        return {
          text: cachedLines.join("\n"),
          lineCount: cachedLines.length,
          isPartial: false,
          hasOldLines: Number(fileObj.deletions || 0) > 0,
          hasNewLines: true,
        };
      }
    } else if (side === "old" && typeof fileObj.old_content === "string") {
      const lines = fileObj.old_content.split("\n");
      return { text: fileObj.old_content, lineCount: lines.length, isPartial: false, hasOldLines: true, hasNewLines: false };
    }

    const rawLines = Array.isArray(fileObj.lines) ? fileObj.lines : [];
    const newLines = [];
    const oldLines = [];
    let hunkCount = 0;
    let firstNewLn = 0;
    let firstOldLn = 0;

    for (let i = 0; i < rawLines.length; i++) {
      const ln = rawLines[i];
      if (!ln) continue;
      const t = ln.type || "ctx";
      if (t === "hunk") {
        if (String(ln.text || "").startsWith("@@")) {
          hunkCount++;
        }
        continue;
      }
      if (t === "add" || t === "ctx") {
        if (!firstNewLn && Number(ln.new) > 0) firstNewLn = Number(ln.new);
        newLines.push(stripPgDiffLinePrefix(ln.text, t));
      }
      if (t === "del" || t === "ctx") {
        if (!firstOldLn && Number(ln.old) > 0) firstOldLn = Number(ln.old);
        oldLines.push(stripPgDiffLinePrefix(ln.text, t));
      }
    }

    const status = (typeof getPgFileStatus === "function") ? getPgFileStatus(fileObj) : String(fileObj.status || "M");
    const chosenLines = side === "old" ? oldLines : (newLines.length > 0 || status !== "D" ? newLines : oldLines);
    const firstLn = side === "old" ? firstOldLn : firstNewLn;
    const isPartial = Boolean(
      status !== "A" &&
      status !== "D" &&
      (hunkCount > 1 || (firstLn > 1) || Boolean(fileObj.truncated))
    );

    return {
      text: chosenLines.join("\n"),
      lineCount: chosenLines.length,
      isPartial,
      hasOldLines: oldLines.length > 0,
      hasNewLines: newLines.length > 0,
    };
  }

  function slugifyPgMarkdownHeading(rawText) {
    return String(rawText || "")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/[*_~[\]()]/g, "")
      .trim()
      .toLowerCase()
      .replace(/[^\w\u0080-\uffff\s-]/g, "")
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "") || "section";
  }

  function extractPgMarkdownHeadings(rawMd) {
    const lines = String(rawMd || "").replace(/\r\n/g, "\n").split("\n");
    const headings = [];
    let inFence = false;
    let fenceMarker = "";
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fenceMatch = line.match(/^\s{0,8}(`{3,}|~{3,})/);
      if (fenceMatch) {
        const marker = fenceMatch[1];
        if (!inFence) {
          inFence = true;
          fenceMarker = marker[0];
        } else if (marker[0] === fenceMarker && line.trim() === marker) {
          inFence = false;
          fenceMarker = "";
        }
        continue;
      }
      if (inFence) continue;
      const m = line.match(/^\s{0,3}(#{1,4})\s+(.+?)\s*#*$/);
      if (m) {
        const level = m[1].length;
        const rawTitle = m[2].trim();
        const cleanTitle = rawTitle.replace(/`([^`]+)`/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\*([^*]+)\*/g, "$1");
        headings.push({
          level,
          text: cleanTitle,
          rawText: rawTitle,
          slug: slugifyPgMarkdownHeading(rawTitle),
          line: i + 1,
        });
      }
    }
    return headings;
  }

  function mapPgMarkdownCodeFenceLang(rawLang) {
    const lang = String(rawLang || "").trim().toLowerCase();
    if (!lang) return { id: "generic", label: "TEXT" };
    if (["ts", "tsx", "typescript"].includes(lang)) return { id: "js", label: "TYPESCRIPT" };
    if (["js", "jsx", "mjs", "cjs", "javascript"].includes(lang)) return { id: "js", label: "JAVASCRIPT" };
    if (["py", "python", "starlark", "bazel", "bzl"].includes(lang)) return { id: "python", label: lang.toUpperCase() };
    if (["sh", "bash", "zsh", "shell", "console"].includes(lang)) return { id: "config", label: lang.toUpperCase() };
    if (["c", "cc", "cpp", "cxx", "h", "hpp", "java", "kt", "kotlin", "objc"].includes(lang)) return { id: "cpp", label: lang.toUpperCase() };
    if (["rs", "rust"].includes(lang)) return { id: "rust", label: "RUST" };
    if (["go", "golang"].includes(lang)) return { id: "go", label: "GO" };
    if (["html", "htm", "xml", "svg"].includes(lang)) return { id: "markup", label: lang.toUpperCase() };
    if (["json", "jsonl", "yaml", "yml", "toml", "ini", "proto", "textproto", "pbtxt", "css"].includes(lang)) {
      return { id: "config", label: lang.toUpperCase() };
    }
    return { id: "generic", label: lang.toUpperCase() };
  }

  function renderPgMarkdownCodeBlockHtml(codeLines, rawLang, scheme = "syntax_intraline") {
    const langInfo = mapPgMarkdownCodeFenceLang(rawLang);
    const rawCode = codeLines.join("\n");
    const highlightedLines = codeLines.map(ln => {
      if (typeof renderHighlightedDiffLineHtml === "function" && langInfo.id !== "generic") {
        return renderHighlightedDiffLineHtml(ln, "ctx", langInfo.id, scheme, null);
      }
      return escapeHtml(ln);
    });
    const encodedRaw = escapeHtml(rawCode);
    const lineCountLabel = `${codeLines.length} line${codeLines.length === 1 ? "" : "s"}`;
    return `<div class="pg-md-code-block" data-lang="${escapeHtml(langInfo.label.toLowerCase())}">
      <div class="pg-md-code-header">
        <span><span class="pg-md-code-lang">${escapeHtml(langInfo.label)}</span> · ${lineCountLabel}</span>
        <button type="button" class="pg-md-code-copy-btn" data-code="${encodedRaw}" onclick="copyPgMarkdownCodeBlock(event, this)" title="Copy code block to clipboard">📋 Copy</button>
      </div>
      <pre class="pg-md-code-pre"><code>${highlightedLines.join("\n")}</code></pre>
    </div>`;
  }

  function renderPgMarkdownInlineHtml(rawText, clNum = 0) {
    const text = String(rawText == null ? "" : rawText);
    if (!text) return "";

    const tokens = [];
    const saveToken = (html) => {
      const idx = tokens.length;
      tokens.push(html);
      return `\u0000PGMDTOK${idx}\u0000`;
    };

    // 1. Protect inline code spans (`...`) first
    let working = text.replace(/`([^`\n]+)`/g, (_m, codeContent) => {
      const trimmed = String(codeContent || "").trim();
      const escapedCode = escapeHtml(codeContent);
      const isHexColor = /^#[0-9a-fA-F]{6}$/.test(trimmed);
      const swatchHtml = isHexColor
        ? `<span class="pg-md-color-swatch" style="background-color:${escapeHtml(trimmed)};" title="${escapeHtml(trimmed)}"></span>`
        : "";
      const tokCls = (typeof _classifyInlineCodeSpanClass === "function") ? _classifyInlineCodeSpanClass(trimmed) : "";
      return saveToken(`<code class="pg-md-inline-code${tokCls}">${escapedCode}${swatchHtml}</code>`);
    });

    // 2. Escape HTML entities on remaining text
    working = escapeHtml(working);

    // 3. Inline images ![alt](url)
    working = working.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, altText, rawUrl) => {
      const cleanUrl = String(rawUrl || "").trim();
      if (!/^(?:https?:\/\/|data:image\/|\/)/i.test(cleanUrl)) {
        return escapeHtml(_m);
      }
      return saveToken(`<img class="pg-md-inline-img" src="${cleanUrl}" alt="${altText}" loading="lazy" style="max-width:100%;border-radius:6px;" />`);
    });

    // 4. Inline links [label](url)
    working = working.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, rawUrl) => {
      const cleanUrl = String(rawUrl || "").trim();
      if (/^javascript:/i.test(cleanUrl)) return label;
      return saveToken(`<a class="pg-md-link" href="${cleanUrl}" target="_blank" rel="noopener noreferrer">${label}</a>`);
    });

    // 5. Bold + Italic, Bold, Italic, Strikethrough
    working = working.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
    working = working.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    working = working.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    working = working.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    working = working.replace(/(^|[^\w])_([^_\n]+)_([^\w]|$)/g, "$1<em>$2</em>$3");
    working = working.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    // 6. Buganizer references (b/123456789) with PolyGerrit hovercard
    working = working.replace(
      /\bb\/(\d{6,12})\b/gi,
      (_m, bugId) => saveToken(
        `<a class="pg-md-link pg-diff-bug-ref" href="https://b.corp.google.com/issues/${bugId}" target="_blank" rel="noopener noreferrer" data-bug-id="${bugId}" onmouseenter="showPgBugHovercard(event, '${bugId}')" onmouseleave="scheduleHidePgBugHovercard()">b/${bugId}</a>`
      )
    );

    // 7. Bare URLs & go/ shortlinks
    working = working.replace(
      /\b(https?:\/\/[^\s<)"']+)/gi,
      (_m, url) => saveToken(`<a class="pg-md-link" href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`)
    );
    working = working.replace(
      /(^|[\s(])(go\/[a-zA-Z0-9_/-]+)/g,
      (_m, pre, goLink) => `${pre}${saveToken(`<a class="pg-md-link" href="http://${goLink}" target="_blank" rel="noopener noreferrer">${goLink}</a>`)}`
    );

    // Restore tokens (supports nested tokens inside links)
    for (let pass = 0; pass < 2; pass++) {
      working = working.replace(/\u0000PGMDTOK(\d+)\u0000/g, (_m, idxStr) => tokens[Number(idxStr)] || "");
    }
    return working;
  }

  function splitPgMarkdownTableRow(line) {
    let trimmed = String(line || "").trim();
    if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
    if (trimmed.endsWith("|") && !trimmed.endsWith("\\|")) trimmed = trimmed.slice(0, -1);
    const cells = [];
    let cur = "";
    let inCode = false;
    for (let i = 0; i < trimmed.length; i++) {
      const ch = trimmed[i];
      if (ch === "\\" && i + 1 < trimmed.length) {
        cur += ch + trimmed[i + 1];
        i++;
        continue;
      }
      if (ch === "`") {
        inCode = !inCode;
        cur += ch;
        continue;
      }
      if (ch === "|" && !inCode) {
        cells.push(cur.trim());
        cur = "";
        continue;
      }
      cur += ch;
    }
    cells.push(cur.trim());
    return cells;
  }

  function isPgMarkdownTableSeparator(line) {
    const trimmed = String(line || "").trim();
    if (!trimmed.includes("|") || !trimmed.includes("-")) return false;
    const cells = splitPgMarkdownTableRow(trimmed);
    if (cells.length === 0) return false;
    return cells.every(c => /^:?-{2,}:?$/.test(c.trim()));
  }

  function parsePgMarkdownListBlock(lines, startIdx, baseIndent, clNum, scheme) {
    const items = [];
    let i = startIdx;
    let listType = "ul";
    let startNumber = 1;

    const firstMatch = String(lines[startIdx] || "").match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
    if (firstMatch && /^\d+\.$/.test(firstMatch[2])) {
      listType = "ol";
      startNumber = parseInt(firstMatch[2], 10) || 1;
    }

    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        // Look ahead to see if the list continues at >= baseIndent
        let nextNonEmpty = i + 1;
        while (nextNonEmpty < lines.length && !lines[nextNonEmpty].trim()) nextNonEmpty++;
        if (nextNonEmpty >= lines.length) break;
        const nextLine = lines[nextNonEmpty];
        const nextIndent = (nextLine.match(/^(\s*)/) || ["", ""])[1].length;
        const nextBullet = nextLine.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
        if (nextIndent > baseIndent || (nextBullet && nextBullet[1].length === baseIndent)) {
          i = nextNonEmpty;
          continue;
        }
        break;
      }

      const m = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
      if (!m) break;
      const indent = m[1].length;
      if (indent < baseIndent) break;
      if (indent > baseIndent) {
        // Attach nested list to previous item if present
        if (items.length > 0) {
          const nested = parsePgMarkdownListBlock(lines, i, indent, clNum, scheme);
          items[items.length - 1].subHtml.push(nested.html);
          i = nested.nextIdx;
          continue;
        }
      }

      const itemIsOrdered = /^\d+\.$/.test(m[2]);
      if (items.length > 0 && ((listType === "ol") !== itemIsOrdered) && indent === baseIndent) {
        break;
      }

      let headText = m[3];
      let taskCheckboxHtml = "";
      const taskMatch = headText.match(/^\[([ xX])\]\s+(.*)$/);
      if (taskMatch) {
        const checked = taskMatch[1].toLowerCase() === "x";
        taskCheckboxHtml = `<input type="checkbox" disabled ${checked ? "checked" : ""} style="margin-right:6px;vertical-align:middle;" />`;
        headText = taskMatch[2];
      }

      const currentItem = {
        headHtml: taskCheckboxHtml + renderPgMarkdownInlineHtml(headText, clNum),
        subHtml: [],
      };
      items.push(currentItem);
      i++;

      // Consume continuation lines, nested lists, or indented fenced code blocks belonging to this item
      while (i < lines.length) {
        const subLine = lines[i];
        if (!subLine.trim()) {
          let look = i + 1;
          while (look < lines.length && !lines[look].trim()) look++;
          if (look >= lines.length) {
            i = look;
            break;
          }
          const lookIndent = (lines[look].match(/^(\s*)/) || ["", ""])[1].length;
          if (lookIndent > baseIndent) {
            i = look;
            continue;
          }
          break;
        }

        const subIndent = (subLine.match(/^(\s*)/) || ["", ""])[1].length;
        const subFence = subLine.match(/^(\s*)(`{3,}|~{3,})\s*([^\s`~]*)/);
        if (subFence && subFence[1].length > baseIndent) {
          const fenceIndent = subFence[1].length;
          const fenceMarker = subFence[2];
          const fenceLang = subFence[3] || "";
          const codeLines = [];
          i++;
          while (i < lines.length) {
            const clLine = lines[i];
            const closeMatch = clLine.match(/^\s*(`{3,}|~{3,})\s*$/);
            if (closeMatch && closeMatch[1][0] === fenceMarker[0] && closeMatch[1].length >= fenceMarker.length) {
              i++;
              break;
            }
            codeLines.push(clLine.startsWith(" ".repeat(fenceIndent)) ? clLine.slice(fenceIndent) : clLine.replace(/^\s{1,4}/, ""));
            i++;
          }
          currentItem.subHtml.push(renderPgMarkdownCodeBlockHtml(codeLines, fenceLang, scheme));
          continue;
        }

        const subBullet = subLine.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
        if (subBullet) {
          if (subBullet[1].length > baseIndent) {
            const nested = parsePgMarkdownListBlock(lines, i, subBullet[1].length, clNum, scheme);
            currentItem.subHtml.push(nested.html);
            i = nested.nextIdx;
            continue;
          }
          break;
        }

        if (subIndent > baseIndent) {
          currentItem.subHtml.push(`<div class="pg-md-li-cont">${renderPgMarkdownInlineHtml(subLine.trim(), clNum)}</div>`);
          i++;
          continue;
        }
        break;
      }
    }

    const tag = listType === "ol" ? "ol" : "ul";
    const startAttr = (listType === "ol" && startNumber !== 1) ? ` start="${startNumber}"` : "";
    const itemsHtml = items.map(it => `<li class="pg-md-li">${it.headHtml}${it.subHtml.join("")}</li>`).join("");
    return {
      html: `<${tag} class="pg-md-${tag}"${startAttr}>${itemsHtml}</${tag}>`,
      nextIdx: i,
    };
  }

  function renderPgMarkdownDocumentHtml(rawMd, opts = {}) {
    const clNum = Number(opts.clNum || state.selectedClNumber || 0);
    const scheme = opts.scheme || state.diffColorScheme || "syntax_intraline";
    const lines = String(rawMd == null ? "" : rawMd).replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let i = 0;
    let tableCount = 0;
    let codeBlockCount = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        i++;
        continue;
      }

      // 1. Fenced code block (```lang or ~~~lang)
      const fenceMatch = line.match(/^(\s{0,3})(`{3,}|~{3,})\s*([^\s`~]*)/);
      if (fenceMatch) {
        const fenceIndent = fenceMatch[1].length;
        const fenceMarker = fenceMatch[2];
        const fenceLang = fenceMatch[3] || "";
        const codeLines = [];
        i++;
        while (i < lines.length) {
          const curLine = lines[i];
          const closeMatch = curLine.match(/^\s{0,4}(`{3,}|~{3,})\s*$/);
          if (closeMatch && closeMatch[1][0] === fenceMarker[0] && closeMatch[1].length >= fenceMarker.length) {
            i++;
            break;
          }
          codeLines.push(fenceIndent > 0 && curLine.startsWith(" ".repeat(fenceIndent)) ? curLine.slice(fenceIndent) : curLine);
          i++;
        }
        codeBlockCount++;
        blocks.push(renderPgMarkdownCodeBlockHtml(codeLines, fenceLang, scheme));
        continue;
      }

      // 2. Horizontal rule (---, ***, ___)
      if (/^\s{0,3}(?:-\s*-\s*-[- ]*|\*\s*\*\s*\*[* ]*|_\s*_\s*_[_ ]*)$/.test(line)) {
        blocks.push('<hr class="pg-md-hr" />');
        i++;
        continue;
      }

      // 3. ATX Heading (# H1 .. ###### H6)
      const headingMatch = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*$/);
      if (headingMatch) {
        const level = headingMatch[1].length;
        const rawTitle = headingMatch[2].trim();
        const slug = slugifyPgMarkdownHeading(rawTitle);
        const inlineHtml = renderPgMarkdownInlineHtml(rawTitle, clNum);
        blocks.push(
          `<h${level} class="pg-md-heading pg-md-h${level}" id="pg-md-h-${escapeHtml(slug)}" data-line="${i + 1}">` +
          `<a class="pg-md-heading-anchor" href="#pg-md-h-${escapeHtml(slug)}" onclick="scrollToPgMarkdownHeading(event, 'pg-md-h-${escapeHtml(slug)}')" title="Jump to section">#</a>` +
          `${inlineHtml}</h${level}>`
        );
        i++;
        continue;
      }

      // 4. GFM Table (Header row + |---|---| separator row)
      if (trimmed.includes("|") && i + 1 < lines.length && isPgMarkdownTableSeparator(lines[i + 1])) {
        const headers = splitPgMarkdownTableRow(line);
        const aligns = splitPgMarkdownTableRow(lines[i + 1]).map(sep => {
          const s = sep.trim();
          if (s.startsWith(":") && s.endsWith(":")) return "center";
          if (s.endsWith(":")) return "right";
          return "left";
        });
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          rows.push(splitPgMarkdownTableRow(lines[i]));
          i++;
        }
        const thHtml = headers.map((h, colIdx) => {
          const align = aligns[colIdx] || "left";
          const styleAttr = align !== "left" ? ` style="text-align:${align};"` : "";
          return `<th${styleAttr}>${renderPgMarkdownInlineHtml(h, clNum)}</th>`;
        }).join("");
        const tbodyHtml = rows.map(r => {
          const tdHtml = headers.map((_h, colIdx) => {
            const cellText = r[colIdx] !== undefined ? r[colIdx] : "";
            const align = aligns[colIdx] || "left";
            const styleAttr = align !== "left" ? ` style="text-align:${align};"` : "";
            return `<td${styleAttr}>${renderPgMarkdownInlineHtml(cellText, clNum)}</td>`;
          }).join("");
          return `<tr>${tdHtml}</tr>`;
        }).join("");
        tableCount++;
        blocks.push(`<div class="pg-md-table-wrap"><table class="pg-md-table"><thead><tr>${thHtml}</tr></thead><tbody>${tbodyHtml}</tbody></table></div>`);
        continue;
      }

      // 5. Blockquote / GitHub Alert (> [!NOTE], > [!TIP], > [!IMPORTANT], > [!WARNING], > [!CAUTION])
      if (/^\s{0,3}>/.test(line)) {
        const quoteLines = [];
        while (i < lines.length && /^\s{0,3}>/.test(lines[i])) {
          quoteLines.push(lines[i].replace(/^\s{0,3}>\s?/, ""));
          i++;
        }
        const alertMatch = (quoteLines[0] || "").match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i);
        if (alertMatch) {
          const alertKind = alertMatch[1].toLowerCase();
          const icons = { note: "ℹ️", tip: "💡", important: "🟣", warning: "⚠️", caution: "🛑" };
          const titles = { note: "Note", tip: "Tip", important: "Important", warning: "Warning", caution: "Caution" };
          const bodyLines = quoteLines.slice(1);
          if (alertMatch[2]) bodyLines.unshift(alertMatch[2]);
          const bodyHtml = bodyLines.map(ql => renderPgMarkdownInlineHtml(ql, clNum)).join("<br/>");
          blocks.push(
            `<div class="pg-md-alert pg-md-alert-${alertKind}">` +
            `<div class="pg-md-alert-title"><span>${icons[alertKind] || "ℹ️"}</span> <span>${titles[alertKind] || "Note"}</span></div>` +
            `<div class="pg-md-alert-body">${bodyHtml}</div></div>`
          );
        } else {
          const quoteHtml = quoteLines.map(ql => renderPgMarkdownInlineHtml(ql, clNum)).join("<br/>");
          blocks.push(`<blockquote class="pg-md-blockquote">${quoteHtml}</blockquote>`);
        }
        continue;
      }

      // 6. Ordered or Unordered List
      const listMatch = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
      if (listMatch) {
        const parsedList = parsePgMarkdownListBlock(lines, i, listMatch[1].length, clNum, scheme);
        blocks.push(parsedList.html);
        i = parsedList.nextIdx;
        continue;
      }

      // 7. Standard Paragraph
      const paraLines = [];
      while (i < lines.length) {
        const pLine = lines[i];
        const pTrim = pLine.trim();
        if (!pTrim) break;
        if (/^\s{0,3}(`{3,}|~{3,})/.test(pLine)) break;
        if (/^\s{0,3}(#{1,6})\s+/.test(pLine)) break;
        if (/^\s{0,3}(?:-\s*-\s*-[- ]*|\*\s*\*\s*\*[* ]*|_\s*_\s*_[_ ]*)$/.test(pLine)) break;
        if (/^\s{0,3}>/.test(pLine)) break;
        if (/^(\s*)([-*+]|\d+\.)\s+/.test(pLine)) break;
        if (pTrim.includes("|") && i + 1 < lines.length && isPgMarkdownTableSeparator(lines[i + 1])) break;
        paraLines.push(pTrim);
        i++;
      }
      if (paraLines.length > 0) {
        blocks.push(`<p class="pg-md-p">${renderPgMarkdownInlineHtml(paraLines.join(" "), clNum)}</p>`);
      } else {
        i++;
      }
    }

    return {
      html: blocks.join("\n"),
      tableCount,
      codeBlockCount,
      lineCount: lines.length,
    };
  }

  function renderPgMarkdownHeaderToggleHtml(clNum, safeFile, safeFileId, mdMode, viewMode, isSbs) {
    return `<div class="pg-md-header-mode-group" id="pgMdHeaderModeGroup-${clNum}-${safeFileId}" role="group" aria-label="Markdown view mode" onclick="event.stopPropagation()">
      <button type="button" class="pg-md-mode-btn ${mdMode === "preview" ? "is-active" : ""}" data-md-mode="preview" onclick="setPgMarkdownViewMode(event, ${clNum}, '${safeFile}', 'preview')" title="Rendered Markdown preview">
        <span aria-hidden="true">📖</span> <span>Preview</span>
      </button>
      <button type="button" class="pg-md-mode-btn ${mdMode === "split" ? "is-active" : ""}" data-md-mode="split" onclick="setPgMarkdownViewMode(event, ${clNum}, '${safeFile}', 'split')" title="Side-by-side Code Diff + Rendered Markdown preview">
        <span aria-hidden="true">◫</span> <span>Split</span>
      </button>
      <button type="button" class="pg-md-mode-btn ${mdMode === "diff" ? "is-active" : ""}" data-md-mode="diff" onclick="setPgMarkdownViewMode(event, ${clNum}, '${safeFile}', 'diff')" title="Raw Markdown code diff">
        <span aria-hidden="true">📝</span> <span>Diff</span>
      </button>
    </div>
    <button
      type="button"
      class="pg-file-diff-mode-btn"
      onclick="togglePgFileDiffMode(event, ${clNum}, '${safeFile}')"
      title="Toggle Side-by-side / Unified diff table (Shortcut: v)"
    ><span class="pg-diff-mode-icon" aria-hidden="true">${isSbs ? "☷" : "☰"}</span> <span>${isSbs ? "Side-by-side" : "Unified"}</span></button>`;
  }

  function renderPgMarkdownViewerHtml(f, clNum, ps, safeFile, safeFileId, status, mdMode) {
    const side = getPgMarkdownPreviewSide(clNum, f.file);
    const newSource = extractPgFileSourceFromDiff(f, clNum, ps, "new");
    const oldSource = extractPgFileSourceFromDiff(f, clNum, ps, "old");
    const activeSource = side === "old" ? oldSource : newSource;
    const headings = extractPgMarkdownHeadings(activeSource.text);
    const rendered = renderPgMarkdownDocumentHtml(activeSource.text, { clNum });

    let bodyContentHtml = "";
    if (side === "compare" && oldSource.hasOldLines && newSource.hasNewLines) {
      const renderedOld = renderPgMarkdownDocumentHtml(oldSource.text, { clNum });
      const renderedNew = renderPgMarkdownDocumentHtml(newSource.text, { clNum });
      bodyContentHtml = `<div class="pg-md-compare-grid">
        <div class="pg-md-compare-col">
          <div class="pg-md-compare-col-header">Base (Old · ${oldSource.lineCount} lines)</div>
          <div class="pg-markdown-body">${renderedOld.html}</div>
        </div>
        <div class="pg-md-compare-col">
          <div class="pg-md-compare-col-header">Patchset ${ps} (New · ${newSource.lineCount} lines)</div>
          <div class="pg-markdown-body">${renderedNew.html}</div>
        </div>
      </div>`;
    } else {
      bodyContentHtml = `<div class="pg-markdown-body" id="pgMarkdownBody-${clNum}-${safeFileId}">${rendered.html}</div>`;
    }

    const tocHtml = headings.length > 1
      ? `<div class="pg-md-toc-strip" id="pgMdTocStrip-${clNum}-${safeFileId}" role="navigation" aria-label="Markdown document outline">
          <span class="pg-md-toc-label">📑 Sections (${headings.length}):</span>
          ${headings.map(h => `<button type="button" class="pg-md-toc-chip level-${h.level}" onclick="scrollToPgMarkdownHeading(event, 'pg-md-h-${escapeHtml(h.slug)}')" title="Jump to ${escapeHtml(h.text)} (L${h.line})">${escapeHtml(h.text)}</button>`).join("")}
        </div>`
      : "";

    const canCompareSides = status === "M" && oldSource.hasOldLines && newSource.hasNewLines;
    const sideSelectorHtml = canCompareSides
      ? `<div class="pg-md-header-mode-group" role="group" aria-label="Markdown revision side">
          <button type="button" class="pg-md-mode-btn ${side === "new" ? "is-active" : ""}" data-md-side="new" onclick="setPgMarkdownPreviewSide(event, ${clNum}, '${safeFile}', ${ps}, 'new')" title="Render Patchset ${ps} (New)">Patchset ${ps}</button>
          <button type="button" class="pg-md-mode-btn ${side === "old" ? "is-active" : ""}" data-md-side="old" onclick="setPgMarkdownPreviewSide(event, ${clNum}, '${safeFile}', ${ps}, 'old')" title="Render Base (Old)">Base</button>
          <button type="button" class="pg-md-mode-btn ${side === "compare" ? "is-active" : ""}" data-md-side="compare" onclick="setPgMarkdownPreviewSide(event, ${clNum}, '${safeFile}', ${ps}, 'compare')" title="Compare Base vs Patchset ${ps} rendered side-by-side">Old vs New</button>
        </div>`
      : "";

    const loadFullBtnHtml = activeSource.isPartial
      ? `<button type="button" class="pg-md-code-copy-btn pg-md-load-full-btn" onclick="loadPgMarkdownFullFile(event, ${clNum}, '${safeFile}', ${ps})" title="Diff contains partial hunks — click to fetch and render the complete Markdown file">↕ Load full file</button>`
      : "";

    const statsParts = [];
    if (headings.length > 0) statsParts.push(`${headings.length} section${headings.length === 1 ? "" : "s"}`);
    if (rendered.tableCount > 0) statsParts.push(`${rendered.tableCount} table${rendered.tableCount === 1 ? "" : "s"}`);
    if (rendered.codeBlockCount > 0) statsParts.push(`${rendered.codeBlockCount} code block${rendered.codeBlockCount === 1 ? "" : "s"}`);
    statsParts.push(`${activeSource.lineCount} lines`);

    return `<div class="pg-markdown-viewer" id="pgMarkdownViewer-${clNum}-${safeFileId}" data-cl="${clNum}" data-ps="${ps}" data-file="${safeFile}" data-md-mode="${escapeHtml(mdMode)}" data-md-side="${escapeHtml(side)}" data-is-partial="${activeSource.isPartial ? "true" : "false"}">
      <div class="pg-markdown-viewer-toolbar">
        <div class="pg-md-toolbar-left">
          <div class="pg-md-header-mode-group" role="group" aria-label="Markdown viewer mode">
            <button type="button" class="pg-md-mode-btn ${mdMode === "preview" ? "is-active" : ""}" data-md-mode="preview" onclick="setPgMarkdownViewMode(event, ${clNum}, '${safeFile}', 'preview')">📖 Rendered Markdown</button>
            <button type="button" class="pg-md-mode-btn ${mdMode === "split" ? "is-active" : ""}" data-md-mode="split" onclick="setPgMarkdownViewMode(event, ${clNum}, '${safeFile}', 'split')">◫ Split (Diff + Preview)</button>
            <button type="button" class="pg-md-mode-btn ${mdMode === "diff" ? "is-active" : ""}" data-md-mode="diff" onclick="setPgMarkdownViewMode(event, ${clNum}, '${safeFile}', 'diff')">📝 Code Diff</button>
          </div>
          ${sideSelectorHtml}
        </div>
        <div class="pg-md-toolbar-right">
          <span class="pg-md-stats-pill">${escapeHtml(statsParts.join(" · "))}</span>
          ${loadFullBtnHtml}
          <button type="button" class="pg-md-code-copy-btn" onclick="copyPgMarkdownSource(event, ${clNum}, '${safeFile}', ${ps})" title="Copy raw Markdown source to clipboard">📋 Copy Markdown</button>
        </div>
      </div>
      ${tocHtml}
      ${bodyContentHtml}
    </div>`;
  }

  function setPgMarkdownViewMode(event, clNum, filePath, mode) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const validMode = (mode === "split" || mode === "diff") ? mode : "preview";
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!state.markdownViewModeByFile) state.markdownViewModeByFile = {};
    state.markdownViewModeByFile[`${num}:${filePath}`] = validMode;

    const safeFileId = String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_");
    if (typeof document !== "undefined") {
      const workspace = document.getElementById(`pgMdWorkspace-${num}-${safeFileId}`);
      const viewerPane = document.getElementById(`pgMdViewerPane-${num}-${safeFileId}`);
      const diffPane = document.getElementById(`pgMdDiffPane-${num}-${safeFileId}`);
      const fileBlock = document.getElementById(`file-diff-${num}-${safeFileId}`);

      if (workspace) {
        workspace.classList.remove("is-mode-preview", "is-mode-split", "is-mode-diff");
        workspace.classList.add(`is-mode-${validMode}`);
        workspace.setAttribute("data-md-mode", validMode);
      }
      if (viewerPane) {
        viewerPane.style.display = validMode === "diff" ? "none" : "";
      }
      if (diffPane) {
        diffPane.style.display = validMode === "preview" ? "none" : "";
      }
      if (fileBlock && typeof fileBlock.querySelectorAll === "function") {
        fileBlock.querySelectorAll("[data-md-mode]").forEach(btn => {
          if (btn.tagName === "BUTTON") {
            btn.classList.toggle("is-active", btn.getAttribute("data-md-mode") === validMode);
          }
        });
      }
    }
    return validMode;
  }

  function setPgMarkdownPreviewSide(event, clNum, filePath, ps, side) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const validSide = (side === "old" || side === "compare") ? side : "new";
    const num = Number(clNum || state.selectedClNumber || 0);
    const patchSet = Number(ps || (state.diffsByCl && state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    if (!state.markdownPreviewSideByFile) state.markdownPreviewSideByFile = {};
    state.markdownPreviewSideByFile[`${num}:${filePath}`] = validSide;

    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    const safeFile = escapeHtml(filePath);
    const safeFileId = escapeHtml(String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_"));
    if (fileObj && typeof document !== "undefined") {
      const viewerPane = document.getElementById(`pgMdViewerPane-${num}-${safeFileId}`);
      if (viewerPane) {
        const status = getPgFileStatus(fileObj);
        const mdMode = getPgMarkdownViewMode(num, filePath);
        viewerPane.innerHTML = renderPgMarkdownViewerHtml(fileObj, num, patchSet, safeFile, safeFileId, status, mdMode);
      }
    }
    return validSide;
  }

  async function loadPgMarkdownFullFile(event, clNum, filePath, ps) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const patchSet = Number(ps || (state.diffsByCl && state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    const cacheKey = `${num}:${patchSet}:${filePath}`;
    if (typeof sidecarFetch === "function") {
      try {
        const resp = await sidecarFetch(`/api/gerrit/file-content?cl=${num}&file=${encodeURIComponent(filePath)}&ps=${patchSet}`);
        if (resp && resp.ok) {
          const data = await resp.json();
          if (data && Array.isArray(data.lines)) {
            if (!state.fileContentCache) state.fileContentCache = {};
            state.fileContentCache[cacheKey] = data.lines;
          }
        }
      } catch (_e) {}
    }
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    if (fileObj && typeof document !== "undefined") {
      const safeFile = escapeHtml(filePath);
      const safeFileId = escapeHtml(String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_"));
      const viewerPane = document.getElementById(`pgMdViewerPane-${num}-${safeFileId}`);
      if (viewerPane) {
        const status = getPgFileStatus(fileObj);
        const mdMode = getPgMarkdownViewMode(num, filePath);
        viewerPane.innerHTML = renderPgMarkdownViewerHtml(fileObj, num, patchSet, safeFile, safeFileId, status, mdMode);
        if (typeof showToast === "function") {
          showToast(`Loaded full Markdown file for ${safeFile} ✓`);
        }
      }
    }
  }

  function scrollToPgMarkdownHeading(event, headingId) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    if (typeof document === "undefined") return;
    const el = document.getElementById(headingId);
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function copyPgMarkdownCodeBlock(event, btnEl) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const code = btnEl && typeof btnEl.getAttribute === "function" ? btnEl.getAttribute("data-code") || "" : "";
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(code).catch(() => {});
    }
    if (btnEl) {
      const orig = btnEl.textContent;
      btnEl.textContent = "✓ Copied";
      setTimeout(() => { btnEl.textContent = orig || "📋 Copy"; }, 1500);
    }
    if (typeof showToast === "function") {
      showToast("Copied Markdown code block to clipboard ✓");
    }
  }

  function copyPgMarkdownSource(event, clNum, filePath, ps) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    const side = getPgMarkdownPreviewSide(num, filePath);
    const extracted = extractPgFileSourceFromDiff(fileObj || { file: filePath, lines: [] }, num, ps, side === "old" ? "old" : "new");
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(extracted.text).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(`Copied Markdown source (${extracted.lineCount} lines) ✓`);
    }
  }

  /* PolyGerrit Rendered HTML File Viewer (<gr-html-viewer>) */
  const PG_HTML_EXTENSIONS = new Set(["html", "htm", "xhtml"]);
  if (typeof window !== "undefined") {
    window.PG_HTML_EXTENSIONS = PG_HTML_EXTENSIONS;
  }

  function isPgHtmlFile(filePath) {
    return PG_HTML_EXTENSIONS.has(getPgCleanFileExtension(filePath));
  }

  function getPgHtmlViewMode(clNum, filePath) {
    if (!state.htmlViewModeByFile) state.htmlViewModeByFile = {};
    const num = Number(clNum || state.selectedClNumber || 0);
    const key = `${num}:${String(filePath || "")}`;
    return state.htmlViewModeByFile[key] || "preview";
  }

  function getPgHtmlPreviewSide(clNum, filePath) {
    if (!state.htmlPreviewSideByFile) state.htmlPreviewSideByFile = {};
    const num = Number(clNum || state.selectedClNumber || 0);
    const key = `${num}:${String(filePath || "")}`;
    return state.htmlPreviewSideByFile[key] || "new";
  }

  function getPgHtmlViewportPreset(clNum, fileId) {
    if (!state.htmlViewportByFile) state.htmlViewportByFile = {};
    return state.htmlViewportByFile[`${clNum}:${fileId}`] || "desktop";
  }

  function extractPgHtmlMetadata(rawHtml) {
    const html = String(rawHtml == null ? "" : rawHtml);
    const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
    const title = titleMatch ? titleMatch[1].replace(/\s+/g, " ").trim() : "";
    const headingCount = (html.match(/<h[1-4]\b[^>]*>/gi) || []).length;
    const tableCount = (html.match(/<table\b[^>]*>/gi) || []).length;
    const preCount = (html.match(/<pre\b[^>]*>/gi) || []).length;
    const isFullDocument = /<!DOCTYPE\s+html|<html\b|<body\b/i.test(html);
    return {
      title,
      headingCount,
      tableCount,
      preCount,
      isFullDocument,
    };
  }

  function renderPgHtmlHeaderToggleHtml(clNum, safeFile, safeFileId, htmlMode, viewMode, isSbs) {
    return `<div class="pg-html-header-mode-group" id="pgHtmlHeaderModeGroup-${clNum}-${safeFileId}" role="group" aria-label="HTML view mode" onclick="event.stopPropagation()">
      <button type="button" class="pg-html-mode-btn ${htmlMode === "preview" ? "is-active" : ""}" data-html-mode="preview" onclick="setPgHtmlViewMode(event, ${clNum}, '${safeFile}', 'preview')" title="Rendered HTML preview">
        <span aria-hidden="true">🌐</span> <span>Preview</span>
      </button>
      <button type="button" class="pg-html-mode-btn ${htmlMode === "split" ? "is-active" : ""}" data-html-mode="split" onclick="setPgHtmlViewMode(event, ${clNum}, '${safeFile}', 'split')" title="Side-by-side Code Diff + Rendered HTML preview">
        <span aria-hidden="true">◫</span> <span>Split</span>
      </button>
      <button type="button" class="pg-html-mode-btn ${htmlMode === "diff" ? "is-active" : ""}" data-html-mode="diff" onclick="setPgHtmlViewMode(event, ${clNum}, '${safeFile}', 'diff')" title="Raw HTML code diff">
        <span aria-hidden="true">📝</span> <span>Diff</span>
      </button>
    </div>
    <button
      type="button"
      class="pg-file-diff-mode-btn"
      onclick="togglePgFileDiffMode(event, ${clNum}, '${safeFile}')"
      title="Toggle Side-by-side / Unified diff table (Shortcut: v)"
    ><span class="pg-diff-mode-icon" aria-hidden="true">${isSbs ? "☷" : "☰"}</span> <span>${isSbs ? "Side-by-side" : "Unified"}</span></button>`;
  }

  function renderPgHtmlStageHtml(clNum, ps, safeFile, safeFileId, side, viewport, oldSource, newSource, activeSource) {
    if (side === "compare" && oldSource.hasOldLines && newSource.hasNewLines) {
      return `<div class="pg-md-compare-grid">
        <div class="pg-md-compare-col">
          <div class="pg-md-compare-col-header">Base (Old · ${oldSource.lineCount} lines)</div>
          <div class="pg-html-stage viewport-${escapeHtml(viewport)}">
            <iframe class="pg-html-preview-iframe" id="pgHtmlIframeOld-${clNum}-${safeFileId}" sandbox="allow-same-origin" srcdoc="${escapeHtml(oldSource.text)}" title="HTML Preview (Base): ${safeFile}" onload="onPgHtmlPreviewIframeLoad(this, ${clNum}, '${safeFileId}')"></iframe>
          </div>
        </div>
        <div class="pg-md-compare-col">
          <div class="pg-md-compare-col-header">Patchset ${ps} (New · ${newSource.lineCount} lines)</div>
          <div class="pg-html-stage viewport-${escapeHtml(viewport)}">
            <iframe class="pg-html-preview-iframe" id="pgHtmlIframe-${clNum}-${safeFileId}" sandbox="allow-same-origin" srcdoc="${escapeHtml(newSource.text)}" title="HTML Preview (Patchset ${ps}): ${safeFile}" onload="onPgHtmlPreviewIframeLoad(this, ${clNum}, '${safeFileId}')"></iframe>
          </div>
        </div>
      </div>`;
    }
    return `<div class="pg-html-stage viewport-${escapeHtml(viewport)}" id="pgHtmlStage-${clNum}-${safeFileId}">
      <iframe class="pg-html-preview-iframe" id="pgHtmlIframe-${clNum}-${safeFileId}" sandbox="allow-same-origin" srcdoc="${escapeHtml(activeSource.text)}" title="HTML Preview: ${safeFile}" onload="onPgHtmlPreviewIframeLoad(this, ${clNum}, '${safeFileId}')"></iframe>
    </div>`;
  }

  function renderPgHtmlToolbarHtml(clNum, ps, safeFile, safeFileId, status, htmlMode, side, viewport, oldSource, newSource, activeSource, meta) {
    const canCompareSides = status === "M" && oldSource.hasOldLines && newSource.hasNewLines;
    const sideSelectorHtml = canCompareSides
      ? `<div class="pg-html-header-mode-group" role="group" aria-label="HTML revision side">
          <button type="button" class="pg-html-mode-btn ${side === "new" ? "is-active" : ""}" data-html-side="new" onclick="setPgHtmlPreviewSide(event, ${clNum}, '${safeFile}', ${ps}, 'new')">Patchset ${ps}</button>
          <button type="button" class="pg-html-mode-btn ${side === "old" ? "is-active" : ""}" data-html-side="old" onclick="setPgHtmlPreviewSide(event, ${clNum}, '${safeFile}', ${ps}, 'old')">Base</button>
          <button type="button" class="pg-html-mode-btn ${side === "compare" ? "is-active" : ""}" data-html-side="compare" onclick="setPgHtmlPreviewSide(event, ${clNum}, '${safeFile}', ${ps}, 'compare')">Old vs New</button>
        </div>`
      : "";
    const viewportSelectorHtml = `<div class="pg-html-header-mode-group" id="pgHtmlViewportGroup-${clNum}-${safeFileId}" role="group" aria-label="HTML preview viewport size">
      <button type="button" class="pg-html-mode-btn ${viewport === "desktop" ? "is-active" : ""}" data-viewport="desktop" onclick="setPgHtmlViewportPreset(event, ${clNum}, '${safeFileId}', 'desktop')" title="Full-width desktop viewport (100%)">🖥️ 100%</button>
      <button type="button" class="pg-html-mode-btn ${viewport === "tablet" ? "is-active" : ""}" data-viewport="tablet" onclick="setPgHtmlViewportPreset(event, ${clNum}, '${safeFileId}', 'tablet')" title="Tablet viewport (768px)">📱 768px</button>
      <button type="button" class="pg-html-mode-btn ${viewport === "mobile" ? "is-active" : ""}" data-viewport="mobile" onclick="setPgHtmlViewportPreset(event, ${clNum}, '${safeFileId}', 'mobile')" title="Mobile viewport (375px)">📱 375px</button>
    </div>`;
    const titleBadgeHtml = meta.title
      ? `<span class="pg-html-title-pill" title="HTML &lt;title&gt;: ${escapeHtml(meta.title)}">🌐 ${escapeHtml(meta.title)}</span>`
      : "";
    const statsParts = [];
    if (meta.headingCount > 0) statsParts.push(`${meta.headingCount} section${meta.headingCount === 1 ? "" : "s"}`);
    if (meta.tableCount > 0) statsParts.push(`${meta.tableCount} table${meta.tableCount === 1 ? "" : "s"}`);
    if (meta.preCount > 0) statsParts.push(`${meta.preCount} code block${meta.preCount === 1 ? "" : "s"}`);
    statsParts.push(`${activeSource.lineCount} lines`);
    const loadFullBtnHtml = activeSource.isPartial
      ? `<button type="button" class="pg-md-code-copy-btn pg-html-load-full-btn" onclick="loadPgHtmlFullFile(event, ${clNum}, '${safeFile}', ${ps})" title="Diff contains partial hunks — click to fetch and render the complete HTML file">↕ Load full file</button>`
      : "";

    return `<div class="pg-html-viewer-toolbar">
      <div class="pg-html-toolbar-left">
        <div class="pg-html-header-mode-group" role="group" aria-label="HTML viewer mode">
          <button type="button" class="pg-html-mode-btn ${htmlMode === "preview" ? "is-active" : ""}" data-html-mode="preview" onclick="setPgHtmlViewMode(event, ${clNum}, '${safeFile}', 'preview')">🌐 Rendered HTML</button>
          <button type="button" class="pg-html-mode-btn ${htmlMode === "split" ? "is-active" : ""}" data-html-mode="split" onclick="setPgHtmlViewMode(event, ${clNum}, '${safeFile}', 'split')">◫ Split (Diff + Preview)</button>
          <button type="button" class="pg-html-mode-btn ${htmlMode === "diff" ? "is-active" : ""}" data-html-mode="diff" onclick="setPgHtmlViewMode(event, ${clNum}, '${safeFile}', 'diff')">📝 Code Diff</button>
        </div>
        ${sideSelectorHtml}
        ${viewportSelectorHtml}
      </div>
      <div class="pg-html-toolbar-right">
        ${titleBadgeHtml}
        <span class="pg-md-stats-pill">${escapeHtml(statsParts.join(" · "))}</span>
        ${loadFullBtnHtml}
        <button type="button" class="pg-md-code-copy-btn" onclick="togglePgHtmlIframeExpand(event, ${clNum}, '${safeFileId}')" title="Toggle expanded iframe height">⤢ Expand</button>
        <button type="button" class="pg-md-code-copy-btn" onclick="openPgHtmlPreviewInNewTab(event, ${clNum}, '${safeFile}', ${ps})" title="Open rendered HTML document in a new browser tab">↗ New Tab</button>
        <button type="button" class="pg-md-code-copy-btn" onclick="copyPgHtmlSource(event, ${clNum}, '${safeFile}', ${ps})" title="Copy raw HTML source to clipboard">📋 Copy HTML</button>
      </div>
    </div>`;
  }

  function renderPgHtmlViewerHtml(f, clNum, ps, safeFile, safeFileId, status, htmlMode) {
    const side = getPgHtmlPreviewSide(clNum, f.file);
    const viewport = getPgHtmlViewportPreset(clNum, safeFileId);
    const newSource = extractPgFileSourceFromDiff(f, clNum, ps, "new");
    const oldSource = extractPgFileSourceFromDiff(f, clNum, ps, "old");
    const activeSource = side === "old" ? oldSource : newSource;
    const meta = extractPgHtmlMetadata(activeSource.text);
    const toolbarHtml = renderPgHtmlToolbarHtml(clNum, ps, safeFile, safeFileId, status, htmlMode, side, viewport, oldSource, newSource, activeSource, meta);
    const stageContentHtml = renderPgHtmlStageHtml(clNum, ps, safeFile, safeFileId, side, viewport, oldSource, newSource, activeSource);

    return `<div class="pg-html-viewer" id="pgHtmlViewer-${clNum}-${safeFileId}" data-cl="${clNum}" data-ps="${ps}" data-file="${safeFile}" data-html-mode="${escapeHtml(htmlMode)}" data-html-side="${escapeHtml(side)}" data-is-partial="${activeSource.isPartial ? "true" : "false"}">
      ${toolbarHtml}
      ${stageContentHtml}
    </div>`;
  }

  function setPgHtmlViewMode(event, clNum, filePath, mode) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const validMode = (mode === "split" || mode === "diff") ? mode : "preview";
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!state.htmlViewModeByFile) state.htmlViewModeByFile = {};
    state.htmlViewModeByFile[`${num}:${filePath}`] = validMode;

    const safeFileId = String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_");
    if (typeof document !== "undefined") {
      const workspace = document.getElementById(`pgHtmlWorkspace-${num}-${safeFileId}`);
      const viewerPane = document.getElementById(`pgHtmlViewerPane-${num}-${safeFileId}`);
      const diffPane = document.getElementById(`pgHtmlDiffPane-${num}-${safeFileId}`);
      const fileBlock = document.getElementById(`file-diff-${num}-${safeFileId}`);

      if (workspace) {
        workspace.classList.remove("is-mode-preview", "is-mode-split", "is-mode-diff");
        workspace.classList.add(`is-mode-${validMode}`);
        workspace.setAttribute("data-html-mode", validMode);
      }
      if (viewerPane) {
        viewerPane.style.display = validMode === "diff" ? "none" : "";
      }
      if (diffPane) {
        diffPane.style.display = validMode === "preview" ? "none" : "";
      }
      if (fileBlock && typeof fileBlock.querySelectorAll === "function") {
        fileBlock.querySelectorAll("[data-html-mode]").forEach(btn => {
          if (btn.tagName === "BUTTON") {
            btn.classList.toggle("is-active", btn.getAttribute("data-html-mode") === validMode);
          }
        });
      }
    }
    return validMode;
  }

  function setPgHtmlPreviewSide(event, clNum, filePath, ps, side) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const validSide = (side === "old" || side === "compare") ? side : "new";
    const num = Number(clNum || state.selectedClNumber || 0);
    const patchSet = Number(ps || (state.diffsByCl && state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    if (!state.htmlPreviewSideByFile) state.htmlPreviewSideByFile = {};
    state.htmlPreviewSideByFile[`${num}:${filePath}`] = validSide;

    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    const safeFile = escapeHtml(filePath);
    const safeFileId = escapeHtml(String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_"));
    if (fileObj && typeof document !== "undefined") {
      const viewerPane = document.getElementById(`pgHtmlViewerPane-${num}-${safeFileId}`);
      if (viewerPane) {
        const status = getPgFileStatus(fileObj);
        const htmlMode = getPgHtmlViewMode(num, filePath);
        viewerPane.innerHTML = renderPgHtmlViewerHtml(fileObj, num, patchSet, safeFile, safeFileId, status, htmlMode);
      }
    }
    return validSide;
  }

  function setPgHtmlViewportPreset(event, clNum, safeFileId, preset) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const validPreset = (preset === "tablet" || preset === "mobile") ? preset : "desktop";
    if (!state.htmlViewportByFile) state.htmlViewportByFile = {};
    state.htmlViewportByFile[`${clNum}:${safeFileId}`] = validPreset;
    if (typeof document !== "undefined") {
      const stage = document.getElementById(`pgHtmlStage-${clNum}-${safeFileId}`);
      if (stage && stage.classList) {
        stage.classList.remove("viewport-desktop", "viewport-tablet", "viewport-mobile");
        stage.classList.add(`viewport-${validPreset}`);
      }
      const group = document.getElementById(`pgHtmlViewportGroup-${clNum}-${safeFileId}`);
      if (group && typeof group.querySelectorAll === "function") {
        group.querySelectorAll("[data-viewport]").forEach(btn => {
          btn.classList.toggle("is-active", btn.getAttribute("data-viewport") === validPreset);
        });
      }
    }
    return validPreset;
  }

  function onPgHtmlPreviewIframeLoad(iframeEl, _clNum, _safeFileId) {
    if (!iframeEl) return;
    try {
      const doc = iframeEl.contentDocument || (iframeEl.contentWindow && iframeEl.contentWindow.document);
      if (doc && doc.documentElement) {
        const h = Math.max(
          doc.documentElement.scrollHeight || 0,
          (doc.body && doc.body.scrollHeight) || 0
        );
        if (h > 300) {
          iframeEl.style.height = `${Math.min(h + 24, 2400)}px`;
        }
      }
    } catch (_e) {}
  }

  function togglePgHtmlIframeExpand(event, clNum, safeFileId) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    if (typeof document === "undefined") return false;
    const iframe = document.getElementById(`pgHtmlIframe-${clNum}-${safeFileId}`);
    if (!iframe || !iframe.classList) return false;
    const expanded = iframe.classList.toggle("is-expanded");
    if (expanded) {
      iframe.style.height = "1800px";
    } else {
      onPgHtmlPreviewIframeLoad(iframe, clNum, safeFileId);
    }
    return expanded;
  }

  function openPgHtmlPreviewInNewTab(event, clNum, filePath, ps) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    const side = getPgHtmlPreviewSide(num, filePath);
    const extracted = extractPgFileSourceFromDiff(fileObj || { file: filePath, lines: [] }, num, ps, side === "old" ? "old" : "new");
    if (typeof Blob !== "undefined" && typeof URL !== "undefined" && typeof URL.createObjectURL === "function" && typeof window !== "undefined" && typeof window.open === "function") {
      const blob = new Blob([extracted.text], { type: "text/html;charset=utf-8" });
      const blobUrl = URL.createObjectURL(blob);
      window.open(blobUrl, "_blank", "noopener,noreferrer");
      return blobUrl;
    }
    return "";
  }

  function copyPgHtmlSource(event, clNum, filePath, ps) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    const side = getPgHtmlPreviewSide(num, filePath);
    const extracted = extractPgFileSourceFromDiff(fileObj || { file: filePath, lines: [] }, num, ps, side === "old" ? "old" : "new");
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(extracted.text).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(`Copied HTML source (${extracted.lineCount} lines) ✓`);
    }
  }

  async function loadPgHtmlFullFile(event, clNum, filePath, ps) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const patchSet = Number(ps || (state.diffsByCl && state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    const cacheKey = `${num}:${patchSet}:${filePath}`;
    if (typeof sidecarFetch === "function") {
      try {
        const resp = await sidecarFetch(`/api/gerrit/file-content?cl=${num}&file=${encodeURIComponent(filePath)}&ps=${patchSet}`);
        if (resp && resp.ok) {
          const data = await resp.json();
          if (data && Array.isArray(data.lines)) {
            if (!state.fileContentCache) state.fileContentCache = {};
            state.fileContentCache[cacheKey] = data.lines;
          }
        }
      } catch (_e) {}
    }
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const fileObj = bundle && Array.isArray(bundle.files) ? bundle.files.find(f => f && f.file === filePath) : null;
    if (fileObj && typeof document !== "undefined") {
      const safeFile = escapeHtml(filePath);
      const safeFileId = escapeHtml(String(filePath || "").replace(/[^a-zA-Z0-9_-]/g, "_"));
      const viewerPane = document.getElementById(`pgHtmlViewerPane-${num}-${safeFileId}`);
      if (viewerPane) {
        const status = getPgFileStatus(fileObj);
        const htmlMode = getPgHtmlViewMode(num, filePath);
        viewerPane.innerHTML = renderPgHtmlViewerHtml(fileObj, num, patchSet, safeFile, safeFileId, status, htmlMode);
        if (typeof showToast === "function") {
          showToast(`Loaded full HTML file for ${safeFile} ✓`);
        }
      }
    }
  }

