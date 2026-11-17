  const _pgFileCodeLinesHtmlCache = new Map();
  const _pgFileDiffBlockHtmlCache = new Map();
  const _pgFileDiffDerivedCache = new Map();
  const _PG_EMPTY_INLINE_COMMENTS = [];
  const PG_FILE_DIFF_CACHE_MAX_ENTRIES = 2000;

  function setPgBoundedMapEntry(map, key, val) {
    if (map.size >= PG_FILE_DIFF_CACHE_MAX_ENTRIES && !map.has(key)) {
      const oldestKey = map.keys().next().value;
      if (oldestKey !== undefined) map.delete(oldestKey);
    }
    map.set(key, val);
  }

  function getPgFileLinesVersionSig(f) {
    const rawLines = (f && Array.isArray(f.lines)) ? f.lines : [];
    const len = rawLines.length;
    if (len === 0) return "0";
    const first = rawLines[0] || {};
    const last = rawLines[len - 1] || {};
    if (
      f._verLinesRef === rawLines &&
      f._verLinesLen === len &&
      f._verFirstRef === first &&
      f._verLastRef === last &&
      f._verFirstText === first.text &&
      f._verLastText === last.text &&
      f._verLinesSig !== undefined
    ) {
      return f._verLinesSig;
    }
    const sig = `${len}:${first.type || ""}:${first.old ?? ""}:${first.new ?? ""}:${(first.text || "").length}:${last.type || ""}:${last.old ?? ""}:${last.new ?? ""}:${(last.text || "").length}`;
    f._verLinesRef = rawLines;
    f._verLinesLen = len;
    f._verFirstRef = first;
    f._verLastRef = last;
    f._verFirstText = first.text;
    f._verLastText = last.text;
    f._verLinesSig = sig;
    return sig;
  }

  function computePgFileCodeLinesCacheKey(f, fallbackCl, ps, viewMode, scheme, prefs, safeFile, linesVersionSig, commentsSig = "0") {
    const hasBlame = hasAnyOwnProp(state.fileBlameCache) && Boolean(state.fileBlameCache[`${fallbackCl}:${ps}:${safeFile}`]);
    const cachedTotalLen = (hasAnyOwnProp(state.fileContentCache) && Array.isArray(state.fileContentCache[`${fallbackCl}:${ps}:${f.file}`]))
      ? state.fileContentCache[`${fallbackCl}:${ps}:${f.file}`].length
      : 0;
    const customTrailing = Boolean(typeof window !== "undefined" && window.renderPgTrailingContextControlBarHtml && window.renderPgTrailingContextControlBarHtml !== renderPgTrailingContextControlBarHtml);
    const intraBit = prefs ? (prefs.intralineDifference !== false ? 1 : 0) : 1;
    const synBit = prefs ? (prefs.syntaxHighlighting !== false ? 1 : 0) : 1;
    const tabBit = prefs ? (prefs.showTabs ? 1 : 0) : 0;
    const wsErrBit = prefs ? (prefs.showWhitespaceErrors !== false ? 1 : 0) : 1;
    const wsMode = prefs ? (prefs.ignoreWhitespace || "none") : "none";
    return `${fallbackCl}|${ps}|${f.file || ""}|${f.status || ""}|${f.total_lines || f.line_count || 0}|${cachedTotalLen}|${hasBlame ? 1 : 0}|${customTrailing ? 1 : 0}|${viewMode}|${scheme}|${intraBit}|${synBit}|${tabBit}|${wsErrBit}|${wsMode}|${linesVersionSig}|${commentsSig}`;
  }

  function renderFileDiffBlock(f, defaultClNum, patchSet, isVisible = true, navInfo = null) {
    const rawLines = Array.isArray(f.lines) ? f.lines : [];
    const firstTaggedCl = (!defaultClNum && rawLines.length > 0)
      ? (rawLines.find(l => Number(l.clNum || 0) > 0) || {}).clNum
      : 0;
    const fallbackCl = +(defaultClNum || firstTaggedCl || state.selectedClNumber || 0);
    const ps = +(patchSet || (state.diffsByCl[fallbackCl] && state.diffsByCl[fallbackCl].patch_set) || 1);
    const isRev = (navInfo && typeof navInfo.isRev === "boolean")
      ? navInfo.isRev
      : isPgFileReviewed(fallbackCl, ps, f.file);
    const isCollapsed = (navInfo && typeof navInfo.isCollapsed === "boolean")
      ? navInfo.isCollapsed
      : isPgFileCollapsed(fallbackCl, ps, f.file);
    const rawOldVal = f.old_path || f.oldPath || f.copied_from || f.renamed_from || "";
    const rawSimVal = f.similarity ?? f.similarity_score ?? null;
    if (
      !f._cachedLangInfo ||
      f._cachedFilePath !== f.file ||
      f._cachedMetaStatusRaw !== f.status ||
      f._cachedMetaOldRaw !== rawOldVal ||
      f._cachedMetaSimRaw !== rawSimVal ||
      f._cachedMetaIsCopyRaw !== f.is_copy ||
      f._cachedMetaIsRenamedRaw !== f.is_renamed
    ) {
      const sameFilePath = f._cachedFilePath === f.file;
      f._cachedFilePath = f.file;
      f._cachedMetaStatusRaw = f.status;
      f._cachedMetaOldRaw = rawOldVal;
      f._cachedMetaSimRaw = rawSimVal;
      f._cachedMetaIsCopyRaw = f.is_copy;
      f._cachedMetaIsRenamedRaw = f.is_renamed;
      if (!sameFilePath || f._cachedSafeFile === undefined) {
        f._cachedSafeFile = escapeHtml(f.file);
      }
      if (!sameFilePath || f._cachedSafeBaseName === undefined) {
        const slashIdx = String(f.file).lastIndexOf("/");
        f._cachedSafeBaseName = escapeHtml(slashIdx >= 0 ? String(f.file).slice(slashIdx + 1) : f.file);
      }
      if (!sameFilePath || f._cachedSafeFileId === undefined) {
        f._cachedSafeFileId = String(f.file).replace(/[^a-zA-Z0-9_-]/g, "_");
      }
      if (!sameFilePath || !f._cachedLangInfo) {
        f._cachedLangInfo = detectDiffFileLanguage(f.file);
      }
      if (
        !sameFilePath ||
        f._cachedExt === undefined ||
        f._cachedIsImg === undefined ||
        f._cachedIsBin === undefined ||
        f._cachedIsMd === undefined ||
        f._cachedIsHtml === undefined
      ) {
        const ext = (typeof getPgFileExtension === "function") ? getPgFileExtension(f.file) : "";
        f._cachedExt = ext;
        const cleanExt = ext && ext.charCodeAt(0) === 46 ? ext.slice(1) : ext;
        const isImgExt = Boolean(cleanExt && PG_IMAGE_EXTENSIONS.has(cleanExt));
        f._cachedIsImg = isImgExt;
        f._cachedIsBin = Boolean(isImgExt || (cleanExt && PG_BINARY_EXTENSIONS.has(cleanExt)));
        f._cachedIsMd = Boolean(cleanExt && PG_MARKDOWN_EXTENSIONS.has(cleanExt));
        f._cachedIsHtml = Boolean(cleanExt && PG_HTML_EXTENSIONS.has(cleanExt));
      }
      f._cachedStatus = ((sameFilePath || f._loopStatusFile === f.file) && f._loopStatus) ? f._loopStatus : getPgFileStatus(f);
      f._cachedOldPath = getPgFileOldPath(f);
      f._cachedSim = getPgFileSimilarity(f, f._cachedStatus);
      f._cachedCsLinkHtml = undefined;
    }
    const status = f._cachedStatus;
    const safeFile = f._cachedSafeFile;
    const safeFileId = f._cachedSafeFileId;
    const fileComments = (navInfo && navInfo.commentIdx)
      ? (navInfo.commentIdx.byFile.get(f.file) || _PG_EMPTY_INLINE_COMMENTS)
      : getInlineCommentsForFile(fallbackCl, f.file);
    const scheme = state.diffColorScheme || "syntax_intraline";
    const fastFbs = f._fbState;
    if (
      fastFbs &&
      fastFbs.fastEligible &&
      f._lastDiffBlockHtml !== undefined &&
      fileComments.length === 0 &&
      fastFbs.cl === fallbackCl &&
      fastFbs.ps === ps &&
      fastFbs.file === f.file &&
      fastFbs.status === f.status &&
      fastFbs.isVisible === Boolean(isVisible) &&
      fastFbs.isRev === Boolean(isRev) &&
      fastFbs.isCollapsed === Boolean(isCollapsed) &&
      fastFbs.truncated === Boolean(f.truncated) &&
      fastFbs.isImgRaw === Boolean(f.is_image) &&
      fastFbs.isBinRaw === Boolean(f.is_binary) &&
      fastFbs.adds === (f.additions || 0) &&
      fastFbs.dels === (f.deletions || 0) &&
      fastFbs.totalLines === (f.total_lines || f.line_count || 0) &&
      fastFbs.oldPath === (f._cachedOldPath || "") &&
      fastFbs.sim === (f._cachedSim ?? "") &&
      fastFbs.linesRef === f.lines &&
      fastFbs.linesLen === rawLines.length &&
      fastFbs.linesVer === f.lines_version &&
      fastFbs.linesFirst === (rawLines.length > 0 ? rawLines[0] : null) &&
      fastFbs.linesLast === (rawLines.length > 0 ? rawLines[rawLines.length - 1] : null) &&
      fastFbs.firstText === (rawLines.length > 0 ? rawLines[0].text : undefined) &&
      fastFbs.lastText === (rawLines.length > 0 ? rawLines[rawLines.length - 1].text : undefined) &&
      (!Array.isArray(f.symbols) || f.symbols.length === 0) &&
      !state.activeCommentEditor &&
      !state.activeChatComposer &&
      !state.activeFileOutlineMenu &&
      !state.continuousStackDiff &&
      fastFbs.scheme === scheme &&
      !hasAnyOwnProp(state.fileDiffModeOverrides) &&
      fastFbs.viewMode === (state.diffViewMode || "side-by-side") &&
      (!state.expandedContextFiles || state.expandedContextFiles.size === 0) &&
      (!state.activeBlameFiles || state.activeBlameFiles.size === 0) &&
      !hasAnyOwnProp(state.fileBlameCache) &&
      !hasAnyOwnProp(state.fileContentCache) &&
      fastFbs.prefsRef === ((navInfo && navInfo.prefs) || getPgDiffPreferences())
    ) {
      const curBundle = state.diffsByCl && state.diffsByCl[fallbackCl];
      const curRawFiles = (curBundle && Array.isArray(curBundle.files)) ? curBundle.files : _PG_EMPTY_ARRAY;
      const curCl = fastFbs.targetClRef;
      const noConflicts =
        (!curBundle || !Array.isArray(curBundle.conflicts) || curBundle.conflicts.length === 0) &&
        (!curCl || !Array.isArray(curCl.conflicts) || curCl.conflicts.length === 0);
      const csMatches = navInfo && navInfo.csRepoKey !== undefined
        ? fastFbs.csRepoKey === navInfo.csRepoKey
        : (!curCl || (
            fastFbs.csBaseUrl === curCl.code_search_base_url &&
            fastFbs.csProj === curCl.project &&
            fastFbs.csBranch === curCl.branch
          ));
      const navInMatches =
        fastFbs.navPrevIn === ((navInfo && navInfo.prevFile) || "") &&
        fastFbs.navNextIn === ((navInfo && navInfo.nextFile) || "") &&
        fastFbs.navIdxIn === ((navInfo && navInfo.index) || 0) &&
        fastFbs.navTotalIn === ((navInfo && navInfo.total) || 0);
      const navNeedsBundle = !fastFbs.navIdxIn || !fastFbs.navTotalIn || (!fastFbs.navPrevIn && !fastFbs.navNextIn && fastFbs.navTotalIn !== 1);
      const navMatches = navInMatches && (
        !navNeedsBundle || (
          fastFbs.bundleRef === curBundle &&
          fastFbs.bundleFilesRef === curRawFiles &&
          fastFbs.bundleFilesLen === curRawFiles.length &&
          fastFbs.usedSortedOrder === Boolean(
            curBundle &&
            Array.isArray(curBundle._cachedSortedDiffFilesRef) &&
            curBundle._cachedSortedDiffFilesLen === curRawFiles.length
          )
        )
      );
      if (noConflicts && csMatches && navMatches) {
        return f._lastDiffBlockHtml;
      }
    }
    const langInfo = f._cachedLangInfo;
    const prefs = (navInfo && navInfo.prefs) || getPgDiffPreferences();
    const isIntraEnabled = prefs.intralineDifference !== false;
    const linesVersionSig = getPgFileLinesVersionSig(f);
    if (f._cachedLinesVersionSig !== linesVersionSig) {
      const canUseDerivedCache = (
        !Array.isArray(f.symbols) &&
        f.file !== "/COMMIT_MSG" &&
        f.file !== "/MERGE_LIST" &&
        rawLines.length > 0 &&
        (typeof window === "undefined" || (
          (!window.computeFileIntralineRanges || window.computeFileIntralineRanges === computeFileIntralineRanges) &&
          (!window.extractPgFileDiffSymbols || window.extractPgFileDiffSymbols === extractPgFileDiffSymbols) &&
          (!window.buildPgDiffOverviewMarkers || window.buildPgDiffOverviewMarkers === buildPgDiffOverviewMarkers)
        ))
      );
      const derivedKey = canUseDerivedCache
        ? `${linesVersionSig}|${rawLines[0].text || ""}|${rawLines[rawLines.length >> 1].text || ""}|${rawLines[rawLines.length - 1].text || ""}`
        : "";
      let derived = derivedKey ? _pgFileDiffDerivedCache.get(derivedKey) : undefined;
      if (!derived) {
        let computedMaxDigits = 4;
        for (let i = 0; i < rawLines.length; i++) {
          const ln = rawLines[i];
          const oldVal = ln.old;
          const newVal = ln.new;
          if (oldVal != null) {
            const oldLen = typeof oldVal === "number"
              ? (oldVal < 10000 ? 4 : (oldVal < 100000 ? 5 : String(oldVal).length))
              : String(oldVal).length;
            if (oldLen > computedMaxDigits) computedMaxDigits = oldLen;
          }
          if (newVal != null) {
            const newLen = typeof newVal === "number"
              ? (newVal < 10000 ? 4 : (newVal < 100000 ? 5 : String(newVal).length))
              : String(newVal).length;
            if (newLen > computedMaxDigits) computedMaxDigits = newLen;
          }
        }
        derived = {
          maxDigits: computedMaxDigits,
          intralineRanges: computeFileIntralineRanges(rawLines),
          symbols: extractPgFileDiffSymbols(f),
          baseOverviewMarkers: buildPgDiffOverviewMarkers(rawLines, _PG_EMPTY_INLINE_COMMENTS),
        };
        if (derivedKey) {
          setPgBoundedMapEntry(_pgFileDiffDerivedCache, derivedKey, derived);
        }
      }
      f._cachedLinesVersionSig = linesVersionSig;
      f._cachedDerivedRef = derivedKey ? derived : null;
      f._cachedMaxDigits = derived.maxDigits;
      f._cachedIntralineRanges = derived.intralineRanges;
      f._cachedSymbols = derived.symbols;
      f._cachedBaseOverviewMarkers = derived.baseOverviewMarkers;
      f._cachedBaseOverviewSig = linesVersionSig;
    }
    const intralineByIdx = (!isIntraEnabled || scheme === "classic") ? {} : (f._cachedIntralineRanges || {});
    const maxDigits = f._cachedMaxDigits || 4;

    const isImg = f._cachedIsImg || Boolean(f.is_image);
    const isBin = Boolean(f.is_binary) || f._cachedIsBin;

    const oldPath = f._cachedOldPath;
    const symbols = Array.isArray(f.symbols) && f.symbols.length > 0 ? f.symbols : (f._cachedSymbols || _PG_EMPTY_ARRAY);

    const bundleObj = state.diffsByCl && state.diffsByCl[fallbackCl];
    const targetClObj = (navInfo && navInfo.change) ||
      resolveBundleChangeObj(bundleObj, fallbackCl) ||
      (state.queue || []).find(ch => Number(ch.number) === fallbackCl) || null;
    const clConflicts = (targetClObj && Array.isArray(targetClObj.conflicts)) ? targetClObj.conflicts : _PG_EMPTY_ARRAY;
    const bundleConflicts = (bundleObj && Array.isArray(bundleObj.conflicts)) ? bundleObj.conflicts : _PG_EMPTY_ARRAY;
    const isConflictFile = (clConflicts.length > 0 && clConflicts.includes(f.file)) || (bundleConflicts.length > 0 && bundleConflicts.includes(f.file));

    let prevFileName = (navInfo && navInfo.prevFile) || null;
    let nextFileName = (navInfo && navInfo.nextFile) || null;
    let fileIndex = (navInfo && navInfo.index) || 0;
    let totalFiles = (navInfo && navInfo.total) || 0;
    if (!fileIndex || !totalFiles || (!prevFileName && !nextFileName && totalFiles !== 1)) {
      const rawFiles = (bundleObj && Array.isArray(bundleObj.files)) ? bundleObj.files : _PG_EMPTY_ARRAY;
      const files = (
        bundleObj &&
        Array.isArray(bundleObj._cachedSortedDiffFilesRef) &&
        bundleObj._cachedSortedDiffFilesLen === rawFiles.length
      )
        ? bundleObj._cachedSortedDiffFilesRef
        : rawFiles;
      if (files.length > 0) {
        if (!totalFiles) totalFiles = files.length;
        let foundIdx = (typeof f._cachedBundleIdx === "number" && files[f._cachedBundleIdx] === f)
          ? f._cachedBundleIdx
          : -1;
        if (foundIdx === -1) {
          foundIdx = files.findIndex(fileItem => fileItem && fileItem.file === f.file);
          if (foundIdx !== -1 && files[foundIdx] === f) {
            f._cachedBundleIdx = foundIdx;
          }
        }
        if (foundIdx !== -1) {
          if (!fileIndex) fileIndex = foundIdx + 1;
          if (prevFileName === null && foundIdx > 0 && files[foundIdx - 1]) {
            prevFileName = files[foundIdx - 1].file;
          }
          if (nextFileName === null && foundIdx < files.length - 1 && files[foundIdx + 1]) {
            nextFileName = files[foundIdx + 1].file;
          }
        }
      }
    }

    const activeStack = state.continuousStackDiff
      ? ((navInfo && navInfo.stack) || (typeof findPgStackForClOrId === "function" ? findPgStackForClOrId(fallbackCl, state.selectedStackId) : null))
      : null;
    const isStackActive = Boolean(state.continuousStackDiff && activeStack && Array.isArray(activeStack.commits) && activeStack.commits.length > 1);
    let prevClNum = null;
    let nextClNum = null;

    if (isStackActive && (!prevFileName || !nextFileName)) {
      const commitIdx = (navInfo && typeof navInfo.commitIdx === "number")
        ? navInfo.commitIdx
        : activeStack.commits.findIndex(c => Number(c.number) === fallbackCl);
      if (commitIdx > 0 && !prevFileName) {
        const prevC = activeStack.commits[commitIdx - 1];
        prevClNum = Number(prevC.number);
        const prevBundle = state.diffsByCl && state.diffsByCl[prevClNum];
        if (prevBundle && Array.isArray(prevBundle.files) && prevBundle.files.length > 0) {
          prevFileName = prevBundle.files[prevBundle.files.length - 1].file;
        }
      }
      if (commitIdx >= 0 && commitIdx < activeStack.commits.length - 1 && !nextFileName) {
        const nextC = activeStack.commits[commitIdx + 1];
        nextClNum = Number(nextC.number);
        const nextBundle = state.diffsByCl && state.diffsByCl[nextClNum];
        if (nextBundle && Array.isArray(nextBundle.files) && nextBundle.files.length > 0) {
          nextFileName = nextBundle.files[0].file;
        }
      }
    }

    const viewMode = getPgDiffViewMode(fallbackCl, f.file);
    const isSbs = viewMode === "side-by-side";
    const isContextExpanded = isPgFileContextExpanded(fallbackCl, f.file, ps);
    const isBlameActive = isPgFileBlameActive(fallbackCl, f.file);
    const sim = f._cachedSim;
    const isPureRename = (status === "R" || status === "C") && oldPath && (sim === 100 || (Number(f.additions || 0) === 0 && Number(f.deletions || 0) === 0 && (!f.lines || f.lines.length === 0 || f.lines.every(l => l.type === "hunk"))));
    const isMd = !isImg && !isBin && !isPureRename && f._cachedIsMd;
    const isHtml = !isImg && !isBin && !isPureRename && !isMd && f._cachedIsHtml;
    const canMemoizeCodeLines =
      !state.activeCommentEditor &&
      !state.activeChatComposer &&
      !isImg &&
      !isBin;
    const commentsSig = (canMemoizeCodeLines && fileComments.length > 0) ? computePgFileCommentsCacheSig(fallbackCl, fileComments) : "";
    const canMemoizeDiffBlock =
      canMemoizeCodeLines &&
      !isMd &&
      !isHtml &&
      !isPureRename;
    let csRepoKey = "";
    if (navInfo && navInfo.csRepoKey !== undefined) {
      csRepoKey = navInfo.csRepoKey;
    } else if (targetClObj) {
      if (
        targetClObj._csBaseUrl !== targetClObj.code_search_base_url ||
        targetClObj._csProj !== targetClObj.project ||
        targetClObj._csBranch !== targetClObj.branch
      ) {
        targetClObj._csBaseUrl = targetClObj.code_search_base_url;
        targetClObj._csProj = targetClObj.project;
        targetClObj._csBranch = targetClObj.branch;
        targetClObj._cachedCsRepoKey = `${targetClObj.code_search_base_url || ""}:${targetClObj.project || ""}:${targetClObj.branch || ""}`;
      }
      csRepoKey = targetClObj._cachedCsRepoKey;
    }
    const isOutlineOpen = Boolean(
      state.activeFileOutlineMenu &&
      Number(state.activeFileOutlineMenu.cl) === fallbackCl &&
      state.activeFileOutlineMenu.file === f.file
    );
    const hasBlame = hasAnyOwnProp(state.fileBlameCache) && Boolean(state.fileBlameCache[`${fallbackCl}:${ps}:${safeFile}`]);
    const cachedTotalLen = (hasAnyOwnProp(state.fileContentCache) && Array.isArray(state.fileContentCache[`${fallbackCl}:${ps}:${f.file}`]))
      ? state.fileContentCache[`${fallbackCl}:${ps}:${f.file}`].length
      : 0;
    const customTrailing = Boolean(typeof window !== "undefined" && window.renderPgTrailingContextControlBarHtml && window.renderPgTrailingContextControlBarHtml !== renderPgTrailingContextControlBarHtml);
    const intraBit = prefs ? (prefs.intralineDifference !== false ? 1 : 0) : 1;
    const synBit = prefs ? (prefs.syntaxHighlighting !== false ? 1 : 0) : 1;
    const tabBit = prefs ? (prefs.showTabs ? 1 : 0) : 0;
    const wsErrBit = prefs ? (prefs.showWhitespaceErrors !== false ? 1 : 0) : 1;
    const wsMode = prefs ? (prefs.ignoreWhitespace || "none") : "none";
    const stateBits =
      (intraBit) |
      (synBit << 1) |
      (tabBit << 2) |
      (wsErrBit << 3) |
      (hasBlame ? 16 : 0) |
      (customTrailing ? 32 : 0) |
      (isVisible ? 64 : 0) |
      (isRev ? 128 : 0) |
      (isCollapsed ? 256 : 0) |
      (isContextExpanded ? 512 : 0) |
      (isBlameActive ? 1024 : 0) |
      (isConflictFile ? 2048 : 0) |
      (f.truncated ? 4096 : 0) |
      (isOutlineOpen ? 8192 : 0);
    if (canMemoizeDiffBlock && f._lastDiffBlockHtml !== undefined && f._fbState) {
      const fbs = f._fbState;
      if (
        fbs.cl === fallbackCl &&
        fbs.ps === ps &&
        fbs.bits === stateBits &&
        fbs.file === f.file &&
        fbs.status === f.status &&
        fbs.totalLines === (f.total_lines || f.line_count || 0) &&
        fbs.cachedTotalLen === cachedTotalLen &&
        fbs.viewMode === viewMode &&
        fbs.scheme === scheme &&
        fbs.wsMode === wsMode &&
        fbs.linesSig === linesVersionSig &&
        fbs.commentsSig === commentsSig &&
        fbs.adds === (f.additions || 0) &&
        fbs.dels === (f.deletions || 0) &&
        fbs.oldPath === (oldPath || "") &&
        fbs.sim === (sim ?? "") &&
        fbs.symLen === symbols.length &&
        fbs.prevFile === (prevFileName || "") &&
        fbs.nextFile === (nextFileName || "") &&
        fbs.fileIdx === (fileIndex || 0) &&
        fbs.totalFiles === (totalFiles || 0) &&
        fbs.prevCl === (prevClNum || 0) &&
        fbs.nextCl === (nextClNum || 0) &&
        fbs.csRepoKey === csRepoKey
      ) {
        fbs.navPrevIn = (navInfo && navInfo.prevFile) || "";
        fbs.navNextIn = (navInfo && navInfo.nextFile) || "";
        fbs.navIdxIn = (navInfo && navInfo.index) || 0;
        fbs.navTotalIn = (navInfo && navInfo.total) || 0;
        return f._lastDiffBlockHtml;
      }
    }
    const codeLinesCacheKey = canMemoizeCodeLines
      ? `${fallbackCl}|${ps}|${f.file || ""}|${f.status || ""}|${f.total_lines || f.line_count || 0}|${cachedTotalLen}|${hasBlame ? 1 : 0}|${customTrailing ? 1 : 0}|${viewMode}|${scheme}|${intraBit}|${synBit}|${tabBit}|${wsErrBit}|${wsMode}|${linesVersionSig}|${commentsSig}`
      : "";
    const diffBlockCacheKey = canMemoizeDiffBlock
      ? `${codeLinesCacheKey}|${isVisible ? 1 : 0}|${isRev ? 1 : 0}|${isCollapsed ? 1 : 0}|${isContextExpanded ? 1 : 0}|${isBlameActive ? 1 : 0}|${isConflictFile ? 1 : 0}|${f.truncated ? 1 : 0}|${f.additions || 0}|${f.deletions || 0}|${oldPath || ""}|${sim ?? ""}|${symbols.length}|${isOutlineOpen ? 1 : 0}|${prevFileName || ""}|${nextFileName || ""}|${fileIndex || 0}|${totalFiles || 0}|${prevClNum || 0}|${nextClNum || 0}|${csRepoKey}`
      : "";
    const saveFastBlockState = (html) => {
      f._lastDiffBlockCacheKey = diffBlockCacheKey;
      f._lastDiffBlockHtml = html;
      const fbs = f._fbState || (f._fbState = {});
      const rawBundleFiles = (bundleObj && Array.isArray(bundleObj.files)) ? bundleObj.files : _PG_EMPTY_ARRAY;
      fbs.fastEligible = Boolean(
        canMemoizeDiffBlock &&
        fileComments.length === 0 &&
        !isConflictFile &&
        !isContextExpanded &&
        !isBlameActive &&
        !hasBlame &&
        cachedTotalLen === 0 &&
        !isOutlineOpen &&
        !customTrailing &&
        (!Array.isArray(f.symbols) || f.symbols.length === 0) &&
        !state.continuousStackDiff &&
        !hasAnyOwnProp(state.fileDiffModeOverrides)
      );
      fbs.usedSortedOrder = Boolean(
        navInfo || (
          bundleObj &&
          Array.isArray(bundleObj._cachedSortedDiffFilesRef) &&
          bundleObj._cachedSortedDiffFilesLen === rawBundleFiles.length
        )
      );
      fbs.cl = fallbackCl;
      fbs.ps = ps;
      fbs.bits = stateBits;
      fbs.file = f.file;
      fbs.status = f.status;
      fbs.totalLines = f.total_lines || f.line_count || 0;
      fbs.cachedTotalLen = cachedTotalLen;
      fbs.viewMode = viewMode;
      fbs.scheme = scheme;
      fbs.wsMode = wsMode;
      fbs.linesSig = linesVersionSig;
      fbs.commentsSig = commentsSig;
      fbs.adds = f.additions || 0;
      fbs.dels = f.deletions || 0;
      fbs.oldPath = oldPath || "";
      fbs.sim = sim ?? "";
      fbs.symLen = symbols.length;
      fbs.prevFile = prevFileName || "";
      fbs.nextFile = nextFileName || "";
      fbs.fileIdx = fileIndex || 0;
      fbs.totalFiles = totalFiles || 0;
      fbs.navPrevIn = (navInfo && navInfo.prevFile) || "";
      fbs.navNextIn = (navInfo && navInfo.nextFile) || "";
      fbs.navIdxIn = (navInfo && navInfo.index) || 0;
      fbs.navTotalIn = (navInfo && navInfo.total) || 0;
      fbs.prevCl = prevClNum || 0;
      fbs.nextCl = nextClNum || 0;
      fbs.csRepoKey = csRepoKey;
      fbs.linesRef = f.lines;
      fbs.linesLen = rawLines.length;
      fbs.linesVer = f.lines_version;
      fbs.linesFirst = rawLines.length > 0 ? rawLines[0] : null;
      fbs.linesLast = rawLines.length > 0 ? rawLines[rawLines.length - 1] : null;
      fbs.firstText = rawLines.length > 0 ? rawLines[0].text : undefined;
      fbs.lastText = rawLines.length > 0 ? rawLines[rawLines.length - 1].text : undefined;
      fbs.isVisible = Boolean(isVisible);
      fbs.isRev = Boolean(isRev);
      fbs.isCollapsed = Boolean(isCollapsed);
      fbs.truncated = Boolean(f.truncated);
      fbs.isImgRaw = Boolean(f.is_image);
      fbs.isBinRaw = Boolean(f.is_binary);
      fbs.prefsRef = prefs;
      fbs.bundleRef = bundleObj;
      fbs.bundleFilesRef = rawBundleFiles;
      fbs.bundleFilesLen = rawBundleFiles.length;
      fbs.targetClRef = targetClObj;
      fbs.csBaseUrl = targetClObj ? targetClObj.code_search_base_url : undefined;
      fbs.csProj = targetClObj ? targetClObj.project : undefined;
      fbs.csBranch = targetClObj ? targetClObj.branch : undefined;
    };
    if (diffBlockCacheKey) {
      if (f._lastDiffBlockCacheKey === diffBlockCacheKey && f._lastDiffBlockHtml !== undefined) {
        saveFastBlockState(f._lastDiffBlockHtml);
        return f._lastDiffBlockHtml;
      }
      const cachedBlockHtml = _pgFileDiffBlockHtmlCache.get(diffBlockCacheKey);
      if (cachedBlockHtml !== undefined) {
        saveFastBlockState(cachedBlockHtml);
        return cachedBlockHtml;
      }
    }

    const statusBadgeHtml = renderPgFileStatusBadgeHtml(status, fallbackCl);
    const renamedTagHtml = renderPgFileRenamePillHtml(f, fallbackCl);
    const symbolsOutlineHtml = renderPgFileOutlineButtonHtml(fallbackCl, f.file, symbols);
    const fileConflictBadgeHtml = isConflictFile
      ? `<span class="pg-file-conflict-pill" title="Merge conflict detected in ${safeFile}">⚠️ Conflict</span>`
      : "";

    let prevShortLabel = "";
    let prevTitle = "Previous file in change (Shortcut: [)";
    if (prevClNum) {
      prevShortLabel = ` (ag/${prevClNum})`;
      prevTitle = `Previous file in stack: ag/${prevClNum} - ${escapeHtml(prevFileName || "base")} (Shortcut: [)`;
    } else if (prevFileName) {
      const prevObj = navInfo && navInfo.prevFileObj;
      let prevSafeFull;
      let prevSafeBase;
      if (prevObj && prevObj.file === prevFileName) {
        if (prevObj._cachedFilePath !== prevFileName || prevObj._cachedSafeFile === undefined || prevObj._cachedSafeBaseName === undefined) {
          prevObj._cachedFilePath = prevFileName;
          prevObj._cachedSafeFile = escapeHtml(prevFileName);
          const sIdx = String(prevFileName).lastIndexOf("/");
          prevObj._cachedSafeBaseName = escapeHtml(sIdx >= 0 ? String(prevFileName).slice(sIdx + 1) : prevFileName);
        }
        prevSafeFull = prevObj._cachedSafeFile;
        prevSafeBase = prevObj._cachedSafeBaseName;
      } else {
        const prevSlashIdx = String(prevFileName).lastIndexOf("/");
        const prevBase = prevSlashIdx >= 0 ? String(prevFileName).slice(prevSlashIdx + 1) : prevFileName;
        prevSafeFull = escapeHtml(prevFileName);
        prevSafeBase = escapeHtml(prevBase);
      }
      prevShortLabel = `: ${prevSafeBase}`;
      prevTitle = `Previous file: ${prevSafeFull} (Shortcut: [)`;
    }

    let nextShortLabel = "";
    let nextTitle = "Next file in change (Shortcut: ])";
    if (nextClNum) {
      nextShortLabel = ` (ag/${nextClNum})`;
      nextTitle = `Next file in stack: ag/${nextClNum} - ${escapeHtml(nextFileName || "file")} (Shortcut: ])`;
    } else if (nextFileName) {
      const nextObj = navInfo && navInfo.nextFileObj;
      let nextSafeFull;
      let nextSafeBase;
      if (nextObj && nextObj.file === nextFileName) {
        if (nextObj._cachedFilePath !== nextFileName || nextObj._cachedSafeFile === undefined || nextObj._cachedSafeBaseName === undefined) {
          nextObj._cachedFilePath = nextFileName;
          nextObj._cachedSafeFile = escapeHtml(nextFileName);
          const sIdx = String(nextFileName).lastIndexOf("/");
          nextObj._cachedSafeBaseName = escapeHtml(sIdx >= 0 ? String(nextFileName).slice(sIdx + 1) : nextFileName);
        }
        nextSafeFull = nextObj._cachedSafeFile;
        nextSafeBase = nextObj._cachedSafeBaseName;
      } else {
        const nextSlashIdx = String(nextFileName).lastIndexOf("/");
        const nextBase = nextSlashIdx >= 0 ? String(nextFileName).slice(nextSlashIdx + 1) : nextFileName;
        nextSafeFull = escapeHtml(nextFileName);
        nextSafeBase = escapeHtml(nextBase);
      }
      nextShortLabel = `: ${nextSafeBase}`;
      nextTitle = `Next file: ${nextSafeFull} (Shortcut: ])`;
    }

    const filePosBadgeHtml = (fileIndex > 0 && totalFiles > 0)
      ? `<span class="pg-file-pos-pill" title="File ${fileIndex} of ${totalFiles} in this change">${fileIndex} of ${totalFiles}</span>`
      : "";
    let linesHtml = (codeLinesCacheKey && f._lastCodeLinesKey === codeLinesCacheKey)
      ? f._lastCodeLinesHtml
      : (codeLinesCacheKey ? _pgFileCodeLinesHtmlCache.get(codeLinesCacheKey) : undefined);
    if (linesHtml === undefined) {
      linesHtml = isSbs
        ? renderFileDiffSideBySideHtml(f, fallbackCl, ps, safeFile, fileComments, scheme, langInfo, intralineByIdx, maxDigits)
        : renderFileDiffUnifiedHtml(f, fallbackCl, ps, safeFile, fileComments, scheme, langInfo, intralineByIdx, maxDigits);
      if (codeLinesCacheKey) {
        f._lastCodeLinesKey = codeLinesCacheKey;
        f._lastCodeLinesHtml = linesHtml;
        setPgBoundedMapEntry(_pgFileCodeLinesHtmlCache, codeLinesCacheKey, linesHtml);
      }
    } else if (codeLinesCacheKey) {
      f._lastCodeLinesKey = codeLinesCacheKey;
      f._lastCodeLinesHtml = linesHtml;
    }

    const colStyle = isSbs
      ? (maxDigits > 4 ? ` style="--ln-old-col:${maxDigits * 8 + 18}px;--ln-new-col:${maxDigits * 8 + 18}px;"` : "")
      : (maxDigits > 5 ? ` style="--ln-old-col:${maxDigits * 8 + 14}px;--ln-new-col:${maxDigits * 8 + 30}px;"` : "");
    const truncHtml = f.truncated
      ? `<div class="code-line code-hunk" data-type="hunk"><span class="ln">…</span><span class="ln">…</span><span>Diff truncated — click "Open in Gerrit ↗" to view remaining lines</span></div>`
      : "";
    const langBadgeHtml = langInfo.badge
      ? `<span class="file-lang-badge" title="Detected syntax language">${escapeHtml(langInfo.badge)}</span>`
      : "";
    const chevronIcon = isCollapsed ? "▶" : "▼";
    const codeTableDisplay = isCollapsed ? ' style="display:none;"' : "";
    const displayStyle = !isVisible ? ' style="display:none;"' : "";

    let bodyHtml = "";
    let modeActionHtml = "";
    let hintText = "💡 Drag to highlight or press 'c' / click line # to Comment or Ask Agent";

    const contextActionHtml = (isImg || isBin) ? "" : `<button
      type="button"
      class="btn pg-btn-header-action pg-btn-file-context ${isContextExpanded ? "is-expanded" : ""}"
      onclick="event.stopPropagation(); togglePgFileContext(event, ${fallbackCl}, '${safeFile}', ${ps})"
      title="${isContextExpanded ? "Restore diff hunks (Shortcut: Shift+Alt+X)" : "Expand all context lines in this file (Shortcut: Shift+Alt+X)"}"
    ><span class="pg-btn-icon" aria-hidden="true">↕</span> <span class="pg-file-context-text">${isContextExpanded ? "Diff hunks only" : "Expand context"}</span></button>`;
    const blameActionHtml = (isImg || isBin) ? "" : `<button
      type="button"
      class="btn pg-btn-header-action pg-btn-file-blame ${isBlameActive ? "is-active" : ""}"
      onclick="event.stopPropagation(); togglePgFileBlame(event, ${fallbackCl}, '${safeFile}', ${ps})"
      title="${isBlameActive ? "Hide blame annotations (Shortcut: b)" : "Show blame annotations (Shortcut: b)"}"
    ><span class="pg-btn-icon" aria-hidden="true">📜</span> <span class="pg-file-blame-text">${isBlameActive ? "Hide blame" : "Blame"}</span> <span class="pg-filter-key-hint">b</span></button>`;
    const nextUnreviewedActionHtml = `<button
      type="button"
      class="btn pg-btn-header-action pg-btn-file-next-unreviewed"
      onclick="event.stopPropagation(); navigatePgUnreviewedFileDiff('next', '${safeFile}', ${fallbackCl})"
      title="Jump to next unreviewed file diff (Shortcut: Alt+])"
    ><span class="pg-btn-icon" aria-hidden="true">⚡</span> <span class="pg-file-next-unrev-text">Next unreviewed</span></button>`;
    const markNextActionHtml = `<button
      type="button"
      class="btn pg-btn-header-action pg-btn-file-mark-next"
      onclick="event.stopPropagation(); markActiveFileReviewedAndGoToNextUnreviewed(${fallbackCl}, '${safeFile}', ${ps})"
      title="Mark this file as reviewed and jump to next unreviewed file (Shortcut: M)"
    ><span class="pg-btn-icon" aria-hidden="true">✓</span> <span class="pg-file-mark-next-text">Mark reviewed & next</span> <span class="pg-filter-key-hint">M</span></button>`;
    const footerNextUnreviewedHtml = `<button
      type="button"
      class="pg-btn-diff-nav pg-btn-file-footer-next-unreviewed"
      onclick="event.stopPropagation(); navigatePgUnreviewedFileDiff('next', '${safeFile}', ${fallbackCl})"
      title="Jump to next unreviewed file diff (Shortcut: Alt+])"
    ><span aria-hidden="true">⚡</span> Next unreviewed</button>`;
    const footerMarkNextActionHtml = `<button
      type="button"
      class="pg-btn-diff-nav pg-btn-file-footer-mark-next"
      onclick="event.stopPropagation(); markActiveFileReviewedAndGoToNextUnreviewed(${fallbackCl}, '${safeFile}', ${ps})"
      title="Mark this file as reviewed and jump to next unreviewed file (Shortcut: M)"
    ><span aria-hidden="true">✓</span> Mark reviewed & next <span class="pg-filter-key-hint">M</span></button>`;
    const footerBlameActionHtml = (isImg || isBin) ? "" : `<button
      type="button"
      class="pg-btn-diff-nav pg-btn-file-footer-blame ${isBlameActive ? "is-active" : ""}"
      onclick="event.stopPropagation(); togglePgFileBlame(event, ${fallbackCl}, '${safeFile}', ${ps})"
      title="${isBlameActive ? "Hide blame annotations (Shortcut: b)" : "Show blame annotations (Shortcut: b)"}"
    ><span aria-hidden="true">📜</span> ${isBlameActive ? "Hide blame" : "Blame"}</button>`;
    const footerContextActionHtml = (isImg || isBin) ? "" : `<button
      type="button"
      class="pg-btn-diff-nav pg-btn-file-footer-context ${isContextExpanded ? "is-expanded" : ""}"
      onclick="event.stopPropagation(); togglePgFileContext(event, ${fallbackCl}, '${safeFile}', ${ps})"
      title="${isContextExpanded ? "Restore diff hunks (Shortcut: Shift+Alt+X)" : "Expand context lines in this file (Shortcut: Shift+Alt+X)"}"
    ><span aria-hidden="true">↕</span> ${isContextExpanded ? "Diff hunks only" : "Context"}</button>`;

    const safeScheme = (scheme === "syntax_intraline" || scheme === "classic" || scheme === "monochrome")
      ? scheme
      : escapeHtml(scheme);
    const safeViewMode = (viewMode === "side-by-side" || viewMode === "unified")
      ? viewMode
      : escapeHtml(viewMode);
    if (isImg) {
      hintText = "🖼️ Image comparison · 2-up, Swipe, and Onion skin modes (Shift+M)";
      modeActionHtml = `<span class="pg-image-format-badge" style="font-size:11px;padding:3px 8px;">${escapeHtml(getPgImageFormat(f.file))}</span>`;
      bodyHtml = renderPgImageViewerHtml(f, fallbackCl, ps, safeFile, safeFileId, status, isCollapsed);
    } else if (isBin) {
      hintText = "📦 Binary file";
      modeActionHtml = `<span class="pg-image-format-badge" style="font-size:11px;padding:3px 8px;">BINARY</span>`;
      bodyHtml = renderPgBinaryFileHtml(f, fallbackCl, ps, safeFile, safeFileId, status, isCollapsed);
    } else if (isPureRename) {
      const isCopy = status === "C";
      hintText = isCopy ? "📋 Copied file (no content changes)" : "⇄ Renamed file (no content changes)";
      modeActionHtml = `<span class="pg-file-similarity-pill is-pure" title="100% similarity (no content changes)">100% match</span>`;
      bodyHtml = `<div class="pg-pure-rename-notice"${codeTableDisplay}>
        <span class="pg-pure-rename-icon" aria-hidden="true">${isCopy ? "📋" : "⇄"}</span>
        <div class="pg-pure-rename-body">
          <span class="pg-pure-rename-title">File ${isCopy ? "copied" : "renamed"} with no content changes</span>
          <span class="pg-pure-rename-sub"><code>${escapeHtml(oldPath)}</code> ➔ <code>${safeFile}</code> · <span class="pg-file-similarity-pill is-pure" title="100% match">100% match</span></span>
        </div>
      </div>`;
    } else if (isMd) {
      const mdMode = getPgMarkdownViewMode(fallbackCl, f.file);
      const safeMdMode = escapeHtml(mdMode);
      hintText = "📖 Markdown file · Toggle Preview, Split, or Code Diff";
      modeActionHtml = renderPgMarkdownHeaderToggleHtml(fallbackCl, safeFile, safeFileId, mdMode, viewMode, isSbs);
      const blameClass = isBlameActive ? " has-blame" : "";
      const codeTableHtml = `<div class="code-table scheme-${safeScheme} diff-mode-${safeViewMode}${blameClass}" data-cl="${fallbackCl}" data-file="${safeFile}"${colStyle}>${linesHtml}${truncHtml}</div>`;
      const mdViewerHtml = renderPgMarkdownViewerHtml(f, fallbackCl, ps, safeFile, safeFileId, status, mdMode);
      const viewerDisplay = mdMode === "diff" ? ' style="display:none;"' : "";
      const diffDisplay = mdMode === "preview" ? ' style="display:none;"' : "";
      bodyHtml = `<div class="pg-md-file-workspace is-mode-${safeMdMode}" id="pgMdWorkspace-${fallbackCl}-${safeFileId}" data-cl="${fallbackCl}" data-file="${safeFile}" data-md-mode="${safeMdMode}"${codeTableDisplay}>
        <div class="pg-md-diff-pane" id="pgMdDiffPane-${fallbackCl}-${safeFileId}"${diffDisplay}>${codeTableHtml}</div>
        <div class="pg-markdown-viewer-pane" id="pgMdViewerPane-${fallbackCl}-${safeFileId}"${viewerDisplay}>${mdViewerHtml}</div>
      </div>`;
    } else if (isHtml) {
      const htmlMode = getPgHtmlViewMode(fallbackCl, f.file);
      const safeHtmlMode = escapeHtml(htmlMode);
      hintText = "🌐 HTML file · Toggle Sandboxed Preview, Split, or Code Diff";
      modeActionHtml = renderPgHtmlHeaderToggleHtml(fallbackCl, safeFile, safeFileId, htmlMode, viewMode, isSbs);
      const blameClass = isBlameActive ? " has-blame" : "";
      const codeTableHtml = `<div class="code-table scheme-${safeScheme} diff-mode-${safeViewMode}${blameClass}" data-cl="${fallbackCl}" data-file="${safeFile}"${colStyle}>${linesHtml}${truncHtml}</div>`;
      const htmlViewerHtml = renderPgHtmlViewerHtml(f, fallbackCl, ps, safeFile, safeFileId, status, htmlMode);
      const viewerDisplay = htmlMode === "diff" ? ' style="display:none;"' : "";
      const diffDisplay = htmlMode === "preview" ? ' style="display:none;"' : "";
      bodyHtml = `<div class="pg-html-file-workspace is-mode-${safeHtmlMode}" id="pgHtmlWorkspace-${fallbackCl}-${safeFileId}" data-cl="${fallbackCl}" data-file="${safeFile}" data-html-mode="${safeHtmlMode}"${codeTableDisplay}>
        <div class="pg-html-diff-pane" id="pgHtmlDiffPane-${fallbackCl}-${safeFileId}"${diffDisplay}>${codeTableHtml}</div>
        <div class="pg-html-viewer-pane" id="pgHtmlViewerPane-${fallbackCl}-${safeFileId}"${viewerDisplay}>${htmlViewerHtml}</div>
      </div>`;
    } else {
      modeActionHtml = `<button
        type="button"
        class="pg-file-diff-mode-btn"
        onclick="togglePgFileDiffMode(event, ${fallbackCl}, '${safeFile}')"
        title="Toggle Side-by-side / Unified for this file (Shortcut: v)"
      ><span class="pg-diff-mode-icon" aria-hidden="true">${isSbs ? "☷" : "☰"}</span> <span>${isSbs ? "Side-by-side" : "Unified"}</span></button>`;
      const blameClass = isBlameActive ? " has-blame" : "";
      bodyHtml = `<div class="code-table scheme-${safeScheme} diff-mode-${safeViewMode}${blameClass}" data-cl="${fallbackCl}" data-file="${safeFile}"${colStyle}${codeTableDisplay}>${linesHtml}${truncHtml}</div>`;
    }

    const footerHtml = `<div class="pg-file-diff-footer" data-cl="${fallbackCl}" data-file="${safeFile}">
      <div class="pg-file-footer-left">
        <label class="pg-file-footer-reviewed" title="Mark ${safeFile} as reviewed (Shortcut: f)" onclick="event.stopPropagation()">
          <input type="checkbox" class="pg-diff-footer-reviewed-cb" data-cl="${fallbackCl}" data-ps="${ps}" data-file="${safeFile}" ${isRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this)">
          <span>Reviewed (f)</span>
        </label>
        ${filePosBadgeHtml}
        <span class="pg-file-footer-delta">+${f.additions} / -${f.deletions}</span>
      </div>
      <div class="pg-file-footer-right">
        <div class="pg-diff-nav-group">
          <button type="button" class="pg-btn-diff-nav btn-chunk-prev" onclick="event.stopPropagation(); navigatePgDiffChunk('prev')" title="Previous diff chunk or comment thread (Shortcut: p)"><span aria-hidden="true">◄</span> Chunk (p)</button>
          <button type="button" class="pg-btn-diff-nav btn-chunk-next" onclick="event.stopPropagation(); navigatePgDiffChunk('next')" title="Next diff chunk or comment thread (Shortcut: n)">Chunk (n) <span aria-hidden="true">►</span></button>
          <button type="button" class="pg-btn-diff-nav btn-diff-prev" onclick="event.stopPropagation(); navigatePgFileDiff('prev', '${safeFile}', ${fallbackCl})" title="${prevTitle}"><span aria-hidden="true">▲</span> Prev${prevShortLabel}</button>
          <button type="button" class="pg-btn-diff-nav btn-diff-up" onclick="event.stopPropagation(); scrollToPgFileList(${fallbackCl})" title="Jump up to file list (Shortcut: u)"><span aria-hidden="true">↑</span> File list</button>
          ${footerContextActionHtml}
          ${footerBlameActionHtml}
          ${footerMarkNextActionHtml}
          ${footerNextUnreviewedHtml}
          <button type="button" class="pg-btn-diff-nav btn-diff-next" onclick="event.stopPropagation(); navigatePgFileDiff('next', '${safeFile}', ${fallbackCl})" title="${nextTitle}"><span aria-hidden="true">▼</span> Next${nextShortLabel}</button>
        </div>
      </div>
    </div>`;

    const overviewBarHtml = (!isImg && !isBin && !isPureRename && Array.isArray(f.lines) && f.lines.length > 0)
      ? renderPgDiffOverviewBarHtml(f, fallbackCl, safeFile, fileComments, linesVersionSig, commentsSig || "0")
      : "";
    const contentHtml = overviewBarHtml
      ? `<div class="pg-file-diff-content">${bodyHtml}${overviewBarHtml}</div>`
      : bodyHtml;

    const blockHtml = `<div id="file-diff-${fallbackCl}-${safeFileId}" class="pg-file-diff-block ${isCollapsed ? "is-collapsed" : ""}" data-cl="${fallbackCl}" data-ps="${ps}" data-file="${safeFile}" data-status="${status}" data-diff-mode="${safeViewMode}" data-is-image="${isImg ? "true" : "false"}" data-is-binary="${isBin ? "true" : "false"}"${displayStyle}>
      <div class="file-header" onclick="onPgFileHeaderClick(event, ${fallbackCl}, ${ps}, '${safeFile}')" title="Click to toggle diff collapse (Shortcut: i)">
        <div class="pg-file-header-left" style="display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap;">
          <button type="button" class="pg-file-diff-toggle-btn" title="Toggle diff collapse (Shortcut: i)" onclick="togglePgFileDiffBlock(event, ${fallbackCl}, ${ps}, '${safeFile}')">
            <span class="pg-file-chevron">${chevronIcon}</span>
          </button>
          <span class="pg-sticky-pin-badge" title="Pinned while scrolling through this file" aria-hidden="true">📌</span>
          ${statusBadgeHtml}
          ${(f._cachedCsLinkHtml !== undefined && f._cachedCsLinkRepoKey === csRepoKey)
            ? f._cachedCsLinkHtml
            : (f._cachedCsLinkRepoKey = csRepoKey, f._cachedCsLinkHtml = renderCodeSearchFileLinkHtml(targetClObj || fallbackCl, f.file))}
          ${fileConflictBadgeHtml}
          <button type="button" class="pg-btn-copy-file-path" onclick="copyPgFilePath(event, '${safeFile}')" title="Copy file path to clipboard (📋)">
            <span aria-hidden="true">📋</span>
          </button>
          ${renamedTagHtml}
          ${langBadgeHtml}
          ${filePosBadgeHtml}
          ${symbolsOutlineHtml}
        </div>
        <div class="pg-file-header-right" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <div class="pg-diff-nav-group">
            <button type="button" class="pg-btn-diff-nav btn-chunk-prev" onclick="event.stopPropagation(); navigatePgDiffChunk('prev')" title="Previous diff chunk or comment thread (Shortcut: p)"><span aria-hidden="true">◄</span> Chunk (p)</button>
            <button type="button" class="pg-btn-diff-nav btn-chunk-next" onclick="event.stopPropagation(); navigatePgDiffChunk('next')" title="Next diff chunk or comment thread (Shortcut: n)">Chunk (n) <span aria-hidden="true">►</span></button>
            <button type="button" class="pg-btn-diff-nav btn-diff-prev" onclick="event.stopPropagation(); navigatePgFileDiff('prev', '${safeFile}', ${fallbackCl})" title="${prevTitle}"><span aria-hidden="true">▲</span> Prev</button>
            <button type="button" class="pg-btn-diff-nav btn-diff-next" onclick="event.stopPropagation(); navigatePgFileDiff('next', '${safeFile}', ${fallbackCl})" title="${nextTitle}"><span aria-hidden="true">▼</span> Next</button>
            <button type="button" class="pg-btn-diff-nav btn-diff-up" onclick="event.stopPropagation(); scrollToPgFileList(${fallbackCl})" title="Jump up to file list (Shortcut: u)"><span aria-hidden="true">↑</span> File list</button>
          </div>
          <span class="file-header-hint">${hintText}</span>
          ${modeActionHtml}
          ${contextActionHtml}
          ${blameActionHtml}
          ${markNextActionHtml}
          ${nextUnreviewedActionHtml}
          <button type="button" class="btn pg-btn-header-action pg-btn-comment-file" onclick="event.stopPropagation(); triggerPgAddComment(${fallbackCl}, '${safeFile}')" title="Add comment on this file (Shortcut: c)"><span class="pg-btn-icon" aria-hidden="true">💬</span> Comment (c)</button>
          <button type="button" class="btn pg-btn-header-action pg-btn-edit-file" onclick="event.stopPropagation(); openPgFileEditor(${fallbackCl}, '${safeFile}', ${ps})" title="Edit ${safeFile} in browser (e)"><span class="pg-btn-icon" aria-hidden="true">✏️</span> Edit</button>
          <span class="pg-file-header-delta">+${f.additions} / -${f.deletions}</span>
          <label class="pg-file-header-reviewed" title="Mark ${safeFile} as reviewed (Shortcut: f)" onclick="event.stopPropagation()">
            <input type="checkbox" class="pg-diff-header-reviewed-cb" data-cl="${fallbackCl}" data-ps="${ps}" data-file="${safeFile}" ${isRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this)">
            <span>Reviewed</span>
          </label>
        </div>
      </div>
      ${contentHtml}
      ${footerHtml}
    </div>`;
    if (diffBlockCacheKey) {
      saveFastBlockState(blockHtml);
      setPgBoundedMapEntry(_pgFileDiffBlockHtmlCache, diffBlockCacheKey, blockHtml);
    }
    return blockHtml;
  }

  function getFullCommitMessage(c, bundle) {
    const fromChange = c && typeof c.commit_message === "string" ? c.commit_message.trim() : "";
    const fromBundle = bundle && typeof bundle.commit_message === "string" ? bundle.commit_message.trim() : "";
    if (fromChange && fromChange.length >= (fromBundle ? fromBundle.length : 0)) {
      return fromChange;
    }
    if (fromBundle) {
      return fromBundle;
    }
    return (c && c.subject) || "";
  }

  function _classifyInlineCodeSpanClass(rawCode) {
    const t = String(rawCode || "").trim();
    if (!t) return "";
    if (/^-{1,2}[a-zA-Z0-9][\w.-]*(?:=.*)?$/.test(t)) return " code-tok-flag";
    if (
      /^(?:\/\/|@[a-zA-Z0-9_-]+\/\/)/.test(t) ||
      /[\\/]/.test(t) ||
      /\.[a-zA-Z0-9]{1,6}(?::\d+)?$/.test(t)
    ) {
      return " code-tok-path";
    }
    if (/\w+\s*\(.*\)$/.test(t) || /::/.test(t)) return " code-tok-fn";
    if (/^[A-Z][A-Z0-9_]{2,}$/.test(t) || /^(?:StatusOr|Status|absl|std)\b/.test(t)) {
      return " code-tok-sym";
    }
    return "";
  }

  function _extractBugIdFromUrl(url) {
    const m = String(url || "").match(/(?:b\.corp\.google\.com\/issues\/|issuetracker\.google\.com\/issues\/|https?:\/\/b\/)(\d{6,12})(?:[/?#]|$)/i);
    return m ? m[1] : "";
  }

  function _buildBugHoverAttrs(bugId) {
    const cleanId = String(bugId || "").replace(/\D+/g, "");
    if (!cleanId) return "";
    return ` data-bug-id="${cleanId}" onmouseenter="showPgBugHovercard(event, '${cleanId}')" onmouseleave="scheduleHidePgBugHovercard()"`;
  }

  function renderInlineMarkdownHtml(rawText, isBugFooter = false) {
    const tokens = [];
    const stash = html => {
      const idx = tokens.length;
      tokens.push(html);
      return `\x00MDTOK${idx}\x00`;
    };

    let s = escapeHtml(String(rawText || ""));

    // 1. Inline code spans (`...`) protected first with semantic token coloring
    s = s.replace(/`([^`\n]+)`/g, (_m, code) => {
      const tokCls = (state.diffColorScheme || "syntax_intraline") === "classic"
        ? ""
        : _classifyInlineCodeSpanClass(code);
      return stash(`<code class="commit-md-inline-code${tokCls}">${code}</code>`);
    });

    // 2. Explicit Markdown links [label](https://...) or [label](file:///...)
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/gi, (_m, label, url) => {
      const bugId = _extractBugIdFromUrl(url);
      return stash(`<a class="ag-link" href="${url}" target="_blank" rel="noopener noreferrer"${_buildBugHoverAttrs(bugId)}>${label}</a>`);
    });
    s = s.replace(/\[([^\]]+)\]\((file:\/\/[^\s)]+)\)/gi, (_m, label, url) =>
      stash(`<span class="ag-link" title="${url}">${label}</span>`)
    );

    // 3. Bare http/https URLs
    s = s.replace(/\b(https?:\/\/[^\s<)]+)/gi, (_m, url) => {
      const bugId = _extractBugIdFromUrl(url);
      return stash(`<a class="ag-link" href="${url}" target="_blank" rel="noopener noreferrer"${_buildBugHoverAttrs(bugId)}>${url}</a>`);
    });

    // 4. Google & internal shortlinks (b/123456, ag/123456, go/slug, cl/123456, crbug.com/...)
    s = s.replace(/\bb\/(\d{6,12})\b/gi, (_m, id) =>
      stash(`<a class="ag-link" href="https://b.corp.google.com/issues/${id}" target="_blank" rel="noopener noreferrer"${_buildBugHoverAttrs(id)}>b/${id}</a>`)
    );
    s = s.replace(/\bag\/(\d{5,12})\b/gi, (_m, id) =>
      stash(`<a class="ag-link" href="https://googleplex-android-review.git.corp.google.com/q/${id}" target="_blank" rel="noopener noreferrer">ag/${id}</a>`)
    );
    s = s.replace(/\bgo\/([a-zA-Z0-9][a-zA-Z0-9_/-]*)/g, (_m, slug) =>
      stash(`<a class="ag-link" href="http://go/${slug}" target="_blank" rel="noopener noreferrer">go/${slug}</a>`)
    );
    s = s.replace(/\bcl\/(\d{6,12})\b/gi, (_m, id) =>
      stash(`<a class="ag-link" href="http://cl/${id}" target="_blank" rel="noopener noreferrer">cl/${id}</a>`)
    );
    s = s.replace(/\bcrbug\.com\/((?:[a-zA-Z0-9_.-]+\/)?\d+)\b/gi, (_m, id) =>
      stash(`<a class="ag-link" href="https://crbug.com/${id}" target="_blank" rel="noopener noreferrer">crbug.com/${id}</a>`)
    );

    // 5. Gerrit Change-Id: I[0-9a-fA-F]{40}
    s = s.replace(/\b(I[0-9a-fA-F]{40})\b/g, (_m, changeId) =>
      stash(`<a class="ag-link commit-changeid-link" href="https://android-review.googlesource.com/q/${changeId}" onclick="onPgChangeIdLinkClick(event, '${changeId}')" title="Jump to or search change ${changeId} (Ctrl+Click to open in Gerrit)">${changeId}</a>`)
    );

    // 6. Reviewer / author email addresses: &lt;user@domain.com&gt; or bare user@domain.com
    s = s.replace(/&lt;([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})&gt;/g, (_m, email) =>
      `&lt;${stash(`<a class="ag-link commit-email-link" href="mailto:${email}" onclick="onPgAuthorEmailClick(event, '${email}')" title="Filter dashboard by ${email} (Ctrl+Click to send email)">${email}</a>`)}&gt;`
    );
    s = s.replace(/\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b/g, (_m, email) =>
      stash(`<a class="ag-link commit-email-link" href="mailto:${email}" onclick="onPgAuthorEmailClick(event, '${email}')" title="Filter dashboard by ${email} (Ctrl+Click to send email)">${email}</a>`)
    );

    if (isBugFooter) {
      s = s.replace(/(^|[\s,])(\d{6,12})(?=$|[\s,])/g, (_m, pre, id) =>
        `${pre}${stash(`<a class="ag-link" href="https://b.corp.google.com/issues/${id}" target="_blank" rel="noopener noreferrer"${_buildBugHoverAttrs(id)}>b/${id}</a>`)}`
      );
    }

    // 7. Bold (**text** or __text__), Strikethrough (~~text~~), and word-bounded Italic (*text* or _text_)
    s = s.replace(/\*\*([^\s*](?:[^*]*[^\s*])?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^\s_](?:[^_]*[^\s_])?)__/g, "<strong>$1</strong>");
    s = s.replace(/~~([^\s~](?:[^~]*[^\s~])?)~~/g, "<del>$1</del>");
    s = s.replace(/(^|[^*])\*([^\s*](?:[^*]*[^\s*])?)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[\s(])_([^\s_](?:[^_]*[^\s_])?)_(?=$|[\s.,;:!?)])/g, "$1<em>$2</em>");

    // Restore stashed tokens (multi-pass for nested tokens like [`code`](url))
    for (let pass = 0; pass < 4 && s.includes("\x00MDTOK"); pass++) {
      s = s.replace(/\x00MDTOK(\d+)\x00/g, (_m, idx) => tokens[Number(idx)] || "");
    }
    return s;
  }

  function _isFooterLine(line) {
    return /^(\s*)(Bug|Fixes|Issue|Test|Tested-by|Change-Id|Original-Change-Id|Reviewed-on|Reviewed-by|Presubmit-Verified|Signed-off-by|Co-authored-by|Acked-by|Cc|Reported-by|Suggested-by|Commit-Ready|Part-of|Release-Notes|BREAKING CHANGE)(\s*:\s*)(.*)$/i.test(line);
  }

  function _renderFooterLineHtml(line) {
    const m = line.match(/^(\s*)(Bug|Fixes|Issue|Test|Tested-by|Change-Id|Original-Change-Id|Reviewed-on|Reviewed-by|Presubmit-Verified|Signed-off-by|Co-authored-by|Acked-by|Cc|Reported-by|Suggested-by|Commit-Ready|Part-of|Release-Notes|BREAKING CHANGE)(\s*:\s*)(.*)$/i);
    if (!m) return renderInlineMarkdownHtml(line, false);
    const leading = escapeHtml(m[1]);
    const rawKey = m[2];
    const key = escapeHtml(rawKey);
    const sep = escapeHtml(m[3]);
    const lowerKey = rawKey.toLowerCase();
    const isBugKey = /^(bug|fixes|issue)$/.test(lowerKey);
    let keyCls = "footer-key-meta";
    if (isBugKey) keyCls = "footer-key-bug";
    else if (lowerKey === "test" || lowerKey === "tested-by") keyCls = "footer-key-test";
    else if (lowerKey === "breaking change") keyCls = "footer-key-breaking";
    else if (lowerKey === "change-id" || lowerKey === "original-change-id") keyCls = "footer-key-changeid";
    else if (/^(reviewed-by|signed-off-by|co-authored-by|acked-by|cc|reported-by|suggested-by)$/.test(lowerKey)) keyCls = "footer-key-reviewer";
    else if (lowerKey === "reviewed-on" || lowerKey === "part-of") keyCls = "footer-key-reviewon";

    const rawVal = m[4].trim();
    let rhs = renderInlineMarkdownHtml(m[4], isBugKey);
    if (lowerKey === "change-id" || lowerKey === "original-change-id") {
      rhs = `<span class="commit-footer-val-changeid">${rhs}</span>`;
    } else if (lowerKey === "test" || lowerKey === "tested-by") {
      rhs = `<span class="commit-footer-val-test">${rhs}</span>`;
    }

    const copyBtnHtml = `<button type="button" class="pg-btn-copy-trailer" onclick="copyPgTrailerValue(event, this)" data-trailer-key="${escapeHtml(rawKey)}" data-trailer-val="${escapeHtml(rawVal)}" title="Copy ${escapeHtml(rawKey)} value to clipboard">📋</button>`;

    return `<div class="commit-msg-footer-line" data-trailer-key="${escapeHtml(lowerKey)}">${leading}<span class="commit-msg-footer-key ${keyCls}">${key}${sep}</span>${rhs}${copyBtnHtml}</div>`;
  }

  function _renderSubjectLineHtml(subjectLine) {
    const scheme = state.diffColorScheme || "syntax_intraline";
    const lenWarnHtml = scheme === "semantic_review" && subjectLine.length > 50
      ? `<span class="commit-len-warn" title="Semantic Commit rule: Subject line exceeds 50 characters">${subjectLine.length}/50 chars</span>`
      : "";
    if (scheme === "classic") {
      return `<div class="commit-msg-subject-line"><span class="commit-subj-desc">${renderInlineMarkdownHtml(subjectLine, false)}</span></div>`;
    }
    const semMatch = subjectLine.match(/^(\s*)([a-zA-Z]+)(\([^)]+\))?(!)?(:\s+)(.+)$/);
    if (semMatch) {
      const [, lead, typeRaw, scopeRaw, bangRaw, colonRaw, descRaw] = semMatch;
      const typeLower = typeRaw.toLowerCase();
      const scopeHtml = scopeRaw ? `<span class="commit-subj-scope">${escapeHtml(scopeRaw)}</span>` : "";
      const colonStr = `${bangRaw || ""}${colonRaw}`;
      return `<div class="commit-msg-subject-line">${escapeHtml(lead)}<span class="commit-subj-type commit-subj-type-${escapeHtml(typeLower)}">${escapeHtml(typeRaw)}</span>${scopeHtml}<span class="commit-subj-colon">${escapeHtml(colonStr)}</span><span class="commit-subj-desc">${renderInlineMarkdownHtml(descRaw, false)}</span>${lenWarnHtml}</div>`;
    }
    const bracketMatch = subjectLine.match(/^(\s*)(\[[^\]]+\])(\s+)(.+)$/);
    if (bracketMatch) {
      const [, lead, tagRaw, spaceRaw, descRaw] = bracketMatch;
      return `<div class="commit-msg-subject-line">${escapeHtml(lead)}<span class="commit-subj-scope">${escapeHtml(tagRaw)}</span><span>${escapeHtml(spaceRaw)}</span><span class="commit-subj-desc">${renderInlineMarkdownHtml(descRaw, false)}</span>${lenWarnHtml}</div>`;
    }
    return `<div class="commit-msg-subject-line"><span class="commit-subj-desc">${renderInlineMarkdownHtml(subjectLine, false)}</span>${lenWarnHtml}</div>`;
  }

  function _resolveFenceLanguageId(langStr) {
    const l = String(langStr || "").trim().toLowerCase();
    if (!l) return "generic";
    if (l === "c++" || l === "cpp" || l === "cc" || l === "c" || l === "h" || l === "hpp" || l === "java" || l === "kotlin" || l === "kt") return "cpp";
    if (l === "py" || l === "python" || l === "bzl" || l === "bazel" || l === "starlark") return "python";
    if (l === "js" || l === "javascript" || l === "ts" || l === "typescript") return "js";
    if (l === "sh" || l === "bash" || l === "zsh" || l === "shell" || l === "json" || l === "yaml" || l === "yml" || l === "toml" || l === "ini") return "config";
    if (l === "html" || l === "xml" || l === "svg") return "markup";
    if (l === "diff" || l === "patch") return "diff";
    return "generic";
  }

  function _renderFencedCodeBlockInnerHtml(codeLines, langStr) {
    const scheme = state.diffColorScheme || "syntax_intraline";
    if (scheme === "classic") {
      return escapeHtml(codeLines.join("\n"));
    }
    const langId = _resolveFenceLanguageId(langStr);
    return codeLines.map(rawLine => {
      if (langId === "diff" || /^[+-](?![+-]{2})/.test(rawLine)) {
        const lineType = rawLine.startsWith("+") ? "add" : (rawLine.startsWith("-") ? "del" : "ctx");
        const rendered = renderHighlightedDiffLineHtml(rawLine, lineType, "generic", scheme, null);
        const cls = lineType === "add" ? "code-add" : (lineType === "del" ? "code-del" : "");
        return cls ? `<span class="${cls}" style="display:inline-block;width:100%;">${rendered}</span>` : rendered;
      }
      return renderHighlightedDiffLineHtml(` ${rawLine}`, "ctx", langId, scheme, null).replace(/^<span class="tok-prefix"> <\/span>/, "");
    }).join("\n");
  }

  function formatCommitMessageHtml(rawMsg, clNum = 0) {
    const text = String(rawMsg || "").trim();
    if (!text) return `<span style="color:var(--g-muted);padding:6px 14px;">(No commit message)</span>`;
    const lines = text.split(/\r?\n/);
    const lineCl = Number(clNum || state.selectedClNumber || 0);
    const msgComments = getInlineCommentsForFile(lineCl, "/COMMIT_MSG");

    // Extract first non-empty line as subject
    let i = 0;
    while (i < lines.length && !lines[i].trim()) i++;
    const subjectLine = i < lines.length ? lines[i] : "";
    const subjectLn = i + 1;
    i++;

    const rows = [];
    if (subjectLine) {
      rows.push({
        startLn: subjectLn,
        endLn: subjectLn,
        rawText: subjectLine,
        html: _renderSubjectLineHtml(subjectLine),
        extraCls: "",
      });
    }

    let paraLines = [];
    let footerLines = [];
    const flushPara = () => {
      if (paraLines.length > 0) {
        const sLn = paraLines[0].ln;
        const eLn = paraLines[paraLines.length - 1].ln;
        rows.push({
          startLn: sLn,
          endLn: eLn,
          rawText: paraLines.map(p => p.text).join("\n"),
          html: `<p class="commit-md-p">${paraLines.map(p => renderInlineMarkdownHtml(p.text, false)).join("\n")}</p>`,
          extraCls: "",
        });
        paraLines = [];
      }
    };
    const flushFooters = () => {
      if (footerLines.length > 0) {
        footerLines.forEach((f, fIdx) => {
          rows.push({
            startLn: f.ln,
            endLn: f.ln,
            rawText: f.text,
            html: _renderFooterLineHtml(f.text),
            extraCls: fIdx === 0 ? "commit-msg-first-footer" : "",
          });
        });
        footerLines = [];
      }
    };

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();
      const curLn = i + 1;

      // Blank line
      if (!trimmed) {
        flushPara();
        i++;
        continue;
      }

      // Fenced code block (``` or ~~~)
      const fenceMatch = trimmed.match(/^(`{3,}|~{3,})(.*)$/);
      if (fenceMatch) {
        flushPara();
        flushFooters();
        const fence = fenceMatch[1];
        const lang = fenceMatch[2].trim();
        const startFenceLn = curLn;
        const rawBlockLines = [line];
        const codeLines = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence)) {
          rawBlockLines.push(lines[i]);
          codeLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) {
          rawBlockLines.push(lines[i]);
          i++;
        }
        const endFenceLn = i;
        const langHtml = lang ? `<div class="commit-md-pre-lang">${escapeHtml(lang)}</div>` : "";
        rows.push({
          startLn: startFenceLn,
          endLn: endFenceLn,
          rawText: rawBlockLines.join("\n"),
          html: `<pre class="commit-md-pre">${langHtml}<code class="commit-md-code-block">${_renderFencedCodeBlockInnerHtml(codeLines, lang)}</code></pre>`,
          extraCls: "",
        });
        continue;
      }

      // Git / Gerrit trailer footer line
      if (_isFooterLine(line)) {
        flushPara();
        footerLines.push({ ln: curLn, text: line });
        i++;
        continue;
      }
      if (footerLines.length > 0) {
        flushFooters();
      }

      // Horizontal rule
      if (/^(---|\*\*\*|___)\s*$/.test(trimmed)) {
        flushPara();
        rows.push({
          startLn: curLn,
          endLn: curLn,
          rawText: line,
          html: `<hr class="commit-md-hr" />`,
          extraCls: "",
        });
        i++;
        continue;
      }

      // Markdown heading (# .. ####)
      const headingMatch = line.match(/^\s*(#{1,4})\s+(.+)$/);
      if (headingMatch) {
        flushPara();
        const level = Math.min(4, headingMatch[1].length);
        rows.push({
          startLn: curLn,
          endLn: curLn,
          rawText: line,
          html: `<div class="commit-md-h commit-md-h${level}">${renderInlineMarkdownHtml(headingMatch[2].trim(), false)}</div>`,
          extraCls: "",
        });
        i++;
        continue;
      }

      // Blockquote (> ...)
      if (/^\s*>\s?/.test(line)) {
        flushPara();
        const qStartLn = curLn;
        const rawQuoteLines = [];
        const quoteLines = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          rawQuoteLines.push(lines[i]);
          quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        const qEndLn = i;
        rows.push({
          startLn: qStartLn,
          endLn: qEndLn,
          rawText: rawQuoteLines.join("\n"),
          html: `<blockquote class="commit-md-quote">${quoteLines.map(q => renderInlineMarkdownHtml(q, false)).join("<br/>")}</blockquote>`,
          extraCls: "",
        });
        continue;
      }

      // Markdown table (| a | b | followed by |---|---|)
      if (
        trimmed.startsWith("|") &&
        i + 1 < lines.length &&
        /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])
      ) {
        flushPara();
        const tStartLn = curLn;
        const rawTableLines = [lines[i], lines[i + 1]];
        const parseCells = row =>
          row.trim().replace(/^\||\|$/g, "").split("|").map(c => renderInlineMarkdownHtml(c.trim(), false));
        const headers = parseCells(lines[i]);
        i += 2;
        const tableRows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          rawTableLines.push(lines[i]);
          tableRows.push(parseCells(lines[i]));
          i++;
        }
        const tEndLn = i;
        const theadHtml = `<thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead>`;
        const tbodyHtml = `<tbody>${tableRows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody>`;
        rows.push({
          startLn: tStartLn,
          endLn: tEndLn,
          rawText: rawTableLines.join("\n"),
          html: `<table class="commit-md-table">${theadHtml}${tbodyHtml}</table>`,
          extraCls: "",
        });
        continue;
      }

      // Unordered or ordered list (-, *, +, •, 1.)
      const listMatch = line.match(/^(\s*)([-*+•]|\d+\.)\s+(.+)$/);
      if (listMatch) {
        flushPara();
        const isOrdered = /\d+\./.test(listMatch[2]);
        const tag = isOrdered ? "ol" : "ul";
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(/^(\s*)([-*+•]|\d+\.)\s+(.+)$/);
          if (!m) {
            if (items.length > 0 && /^\s{2,}\S/.test(lines[i]) && !_isFooterLine(lines[i])) {
              items[items.length - 1].text += " " + lines[i].trim();
              items[items.length - 1].rawLines.push(lines[i]);
              items[items.length - 1].endLn = i + 1;
              i++;
              continue;
            }
            break;
          }
          const indentPx = Math.min(48, Math.floor(m[1].length / 2) * 14);
          items.push({
            indentPx,
            text: m[3],
            marker: m[2],
            startLn: i + 1,
            endLn: i + 1,
            rawLines: [lines[i]],
          });
          i++;
        }
        items.forEach((it, itemIdx) => {
          const startAttr = isOrdered ? ` start="${parseInt(it.marker, 10) || (itemIdx + 1)}"` : "";
          const liHtml = `<li class="commit-md-li" ${it.indentPx ? `style="margin-left:${it.indentPx}px"` : ""}>${renderInlineMarkdownHtml(it.text, false)}</li>`;
          rows.push({
            startLn: it.startLn,
            endLn: it.endLn,
            rawText: it.rawLines.join("\n"),
            html: `<${tag} class="commit-md-list"${startAttr}>${liHtml}</${tag}>`,
            extraCls: "",
          });
        });
        continue;
      }

      paraLines.push({ ln: curLn, text: line });
      i++;
    }

    flushPara();
    flushFooters();

    return rows.map((row, idx) => {
      const lnLabel = row.startLn === row.endLn ? String(row.startLn) : `${row.startLn}–${row.endLn}`;
      const threadCls = getLineThreadIndicatorClass(
        msgComments,
        String(row.startLn),
        String(row.endLn),
        row.startLn,
        idx
      );
      const inlineCommentsHtml = renderInlineLineCommentsHtml(
        lineCl,
        "/COMMIT_MSG",
        String(row.startLn),
        String(row.endLn),
        msgComments,
        row.startLn,
        idx
      );
      const msgSummary = getPgLineCommentSummary(
        msgComments,
        String(row.endLn),
        "right",
        idx,
        "/COMMIT_MSG",
        row.startLn,
        row.endLn
      );
      const msgBadge = msgSummary ? renderPgGutterCommentBadgeHtml(msgSummary, lineCl, "/COMMIT_MSG", "right", lnLabel) : "";
      const msgHasComment = msgSummary ? "has-comment-badge" : "";
      return `<div class="code-line commit-msg-line ${row.extraCls || ""} ${threadCls}" data-cl="${lineCl}" data-file="/COMMIT_MSG" data-idx="${idx}" data-start-ln="${row.startLn}" data-old-ln="${row.startLn}" data-new-ln="${row.endLn}" data-type="msg" data-raw-text="${escapeHtml(row.rawText)}"><span class="ln ln-new ln-commit-msg ${msgHasComment}" onclick="onDiffLineGutterClick(event, this)" oncontextmenu="onDiffLineGutterContextMenu(event, this)" data-idx="${idx}" data-side="right" title="Line ${escapeHtml(lnLabel)} · Click to focus · Right-click or press 'y' to copy permalink · Click or press 'c' to Comment or Ask Agent on commit message line · Shift+Click for range"><span class="ln-add-btn" title="Add comment on commit message line (Alt+Click to Ask Agent)">+</span>${msgBadge}${escapeHtml(lnLabel)}</span><div class="code-text commit-msg-cell">${row.html}</div></div>${inlineCommentsHtml}`;
    }).join("");
  }

  const COMMIT_MSG_PREVIEW_LINE_LIMIT = 12;

  let _lastCommitMsgLinesInput = null;
  let _lastCommitMsgLinesVal = 0;

  function getCommitMessageTotalLines(msg) {
    if (!msg) return 0;
    const s = String(msg);
    if (s === _lastCommitMsgLinesInput) return _lastCommitMsgLinesVal;
    const trimmed = s.trim();
    if (!trimmed) {
      _lastCommitMsgLinesInput = s;
      _lastCommitMsgLinesVal = 0;
      return 0;
    }
    let count = 1;
    for (let i = 0; i < trimmed.length; i++) {
      if (trimmed.charCodeAt(i) === 10) count++;
    }
    _lastCommitMsgLinesInput = s;
    _lastCommitMsgLinesVal = count;
    return count;
  }

  function isCommitMsgCollapsed(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    return Boolean(state.collapsedCommitMsgs && state.collapsedCommitMsgs.has(num));
  }

  function isCommitMsgExpanded(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    return Boolean(state.expandedCommitMsgs && state.expandedCommitMsgs.has(num));
  }

  function uncollapseCommitMessage(clNum, expandFull = false) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const ps = Number(selPsMeta.ps || 1);
    setPgFileCollapsed(num, ps, "/COMMIT_MSG", false);
    if (expandFull) {
      if (!state.expandedCommitMsgs) state.expandedCommitMsgs = new Set();
      state.expandedCommitMsgs.add(num);
      const panel = document.getElementById(`commitMsgPanel-${num}`);
      const expandBtn = document.getElementById(`btnCommitMsgExpand-${num}`);
      const expandText = document.getElementById(`btnCommitMsgExpandText-${num}`);
      const expandIcon = expandBtn ? expandBtn.querySelector(".commit-msg-expand-icon") : null;
      if (panel) {
        panel.classList.add("is-expanded");
        panel.classList.remove("is-preview");
      }
      if (expandBtn) {
        expandBtn.classList.add("is-expanded");
        expandBtn.title = "Collapse to preview (Shortcut: z)";
      }
      if (expandText) {
        expandText.textContent = "Show less (z)";
      }
      if (expandIcon) {
        expandIcon.textContent = "▴";
      }
    }
  }

  function renderCommitMessagePanelHtml(c, bundle) {
    if (!c) return "";
    const fullMsg = getFullCommitMessage(c, bundle);
    const isCollapsed = isCommitMsgCollapsed(c.number);
    const commitMsgComments = (typeof getInlineCommentsForFile === "function") ? getInlineCommentsForFile(c.number, "/COMMIT_MSG") : [];
    const hasCommitMsgQa = commitMsgComments.some(cm => cm.kind === "agent_question");
    const isExpanded = isCommitMsgExpanded(c.number) || hasCommitMsgQa;
    const scheme = state.diffColorScheme || "polygerrit_native";
    const isEditing = Boolean(state.editingCommitMsgByCl && state.editingCommitMsgByCl[c.number]);
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[c.number]) || {};
    const curPs = Number(selPsMeta.ps || (bundle && bundle.patch_set) || c.patch_set || 1);
    const isCommitMsgRev = (typeof isPgFileReviewed === "function") && isPgFileReviewed(c.number, curPs, "/COMMIT_MSG");
    const hasActiveCommitMsgSel = Boolean(state.codeSelection && Number(state.codeSelection.clNum) === Number(c.number) && state.codeSelection.filePath === "/COMMIT_MSG");
    const canCacheCommitMsg = !isEditing && commitMsgComments.length === 0 && !hasActiveCommitMsgSel;
    if (canCacheCommitMsg && typeof c._cachedCommitMsgPanelHtml === "string") {
      const cmSub = c._cmPanSub;
      if (
        cmSub &&
        cmSub.num === c.number &&
        cmSub.ps === curPs &&
        cmSub.col === isCollapsed &&
        cmSub.exp === isExpanded &&
        cmSub.scheme === scheme &&
        cmSub.rev === isCommitMsgRev &&
        cmSub.msg === fullMsg
      ) {
        return c._cachedCommitMsgPanelHtml;
      }
    }
    const commitMsgCacheKey = canCacheCommitMsg
      ? `${c.number}|${curPs}|${isCollapsed ? 1 : 0}|${isExpanded ? 1 : 0}|${scheme}|${isCommitMsgRev ? 1 : 0}|${fullMsg}`
      : "";
    if (canCacheCommitMsg && c._cachedCommitMsgPanelKey === commitMsgCacheKey && typeof c._cachedCommitMsgPanelHtml === "string") {
      const wSub = c._cmPanSub || (c._cmPanSub = {});
      wSub.num = c.number;
      wSub.ps = curPs;
      wSub.col = isCollapsed;
      wSub.exp = isExpanded;
      wSub.scheme = scheme;
      wSub.rev = isCommitMsgRev;
      wSub.msg = fullMsg;
      return c._cachedCommitMsgPanelHtml;
    }

    const totalLines = getCommitMessageTotalLines(fullMsg);
    const isLongMsg = totalLines > COMMIT_MSG_PREVIEW_LINE_LIMIT;
    const isPreview = isLongMsg && !isExpanded;
    const editorMode = (state.commitMsgEditorModeByCl && state.commitMsgEditorModeByCl[c.number]) || "write";
    const draftText = (state.commitMsgDraftByCl && typeof state.commitMsgDraftByCl[c.number] === "string")
      ? state.commitMsgDraftByCl[c.number]
      : fullMsg;
    const metrics = (typeof validatePgCommitMessage === "function")
      ? validatePgCommitMessage(draftText)
      : { subjectLen: 0, subjectValid: true, maxLineLen: 0, typePrefix: "", hasBugFooter: false, hasTestFooter: false, hasChangeId: false };
    const metricsHtml = (typeof renderPgCommitMsgMetricsHtml === "function")
      ? renderPgCommitMsgMetricsHtml(metrics)
      : "";

    const linesMetaHtml = totalLines > 0
      ? `<span class="commit-msg-meta" title="${totalLines} lines in commit message">· ${totalLines} line${totalLines === 1 ? "" : "s"}</span>`
      : "";

    const expandBarHtml = isLongMsg ? `
      <div class="commit-msg-expand-wrap">
        <button id="btnCommitMsgExpand-${c.number}" class="commit-msg-expand-bar ${isExpanded ? "is-expanded" : ""}" onclick="toggleCommitMessageExpand(${c.number})" title="${isExpanded ? "Collapse to preview (Shortcut: z)" : "Show all lines (Shortcut: z)"}">
          <span class="commit-msg-expand-icon">${isExpanded ? "▴" : "▾"}</span>
          <span id="btnCommitMsgExpandText-${c.number}">${isExpanded ? "Show less (z)" : `Show all ${totalLines} lines (z)`}</span>
        </button>
      </div>` : "";

    const formattedFullMsgHtml = formatCommitMessageHtml(fullMsg, c.number);
    const formattedDraftMsgHtml = draftText === fullMsg ? formattedFullMsgHtml : formatCommitMessageHtml(draftText, c.number);
    const panelHtml = `<div id="commitMsgPanel-${c.number}" class="commit-msg-panel ${isCollapsed ? "collapsed is-collapsed" : ""} ${isPreview ? "is-preview" : (isExpanded ? "is-expanded" : "")} ${isEditing ? "is-editing" : ""}" data-cl="${c.number}" data-file="/COMMIT_MSG" data-ps="${curPs}" data-lines="${totalLines}">
      <div id="commitMsgBox-${c.number}" style="display:none;" aria-hidden="true"></div>
      <div class="commit-msg-bar" onclick="onPgCommitMsgBarClick(event, ${c.number})" title="Click to toggle commit message visibility (Shortcut: z or i)">
        <div class="pg-commit-msg-bar-left" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <button type="button" class="pg-file-diff-toggle-btn pg-commit-msg-toggle-btn" title="Toggle commit message visibility (Shortcut: z or i)" onclick="event.stopPropagation(); toggleCommitMessage(${c.number})">
            <span class="pg-file-chevron">${isCollapsed ? "▶" : "▼"}</span>
          </button>
          <span class="pg-file-status-badge status-M" title="Commit Message">M</span>
          <span class="commit-msg-title">Commit Message</span>
          ${linesMetaHtml}
          <span class="file-header-hint">Drag text or click line # to Comment / Ask Agent</span>
        </div>
        <div class="commit-msg-actions">
          <div class="pg-diff-nav-group pg-commit-msg-nav-group">
            <button type="button" class="pg-btn-diff-nav btn-chunk-next" onclick="event.stopPropagation(); navigatePgDiffChunk('next')" title="Next diff chunk or comment thread (Shortcut: n)">Chunk (n) <span aria-hidden="true">►</span></button>
            <button type="button" class="pg-btn-diff-nav btn-diff-next" onclick="event.stopPropagation(); navigatePgFileDiff('next', '/COMMIT_MSG', ${c.number})" title="Next file diff"><span aria-hidden="true">▼</span> Next</button>
            <button type="button" class="pg-btn-diff-nav btn-diff-up" onclick="event.stopPropagation(); scrollToPgFileList(${c.number})" title="Jump up to file list (Shortcut: u)"><span aria-hidden="true">↑</span> File list</button>
          </div>
          <button id="btnEditCommitMsg-${c.number}" class="commit-msg-btn pg-btn-edit-commit-msg" onclick="event.stopPropagation(); openPgCommitMsgEditor(${c.number})" title="Edit commit message in browser (Shortcut: e on /COMMIT_MSG)"><span class="pg-btn-icon" aria-hidden="true">✏️</span> Edit</button>
          <button class="commit-msg-btn" onclick="event.stopPropagation(); selectFullCommitMessage(${c.number}, 'comment')" title="Add comment on commit message (Shortcut: c)">💬 Comment</button>
          <button class="commit-msg-btn" onclick="event.stopPropagation(); selectFullCommitMessage(${c.number}, 'ask')">🤖 Ask Agent</button>
          <button id="btnCopyCommitMsg-${c.number}" class="commit-msg-btn" onclick="event.stopPropagation(); copyCommitMessage(${c.number}, this)" title="Copy full commit message to clipboard">📋 Copy</button>
          <button id="commitMsgToggleBtn-${c.number}" class="commit-msg-btn" onclick="event.stopPropagation(); toggleCommitMessage(${c.number})" title="Toggle commit message visibility (Shortcut: z or i)">${isCollapsed ? "Show ▾" : "Hide ▴"}</button>
          <label class="pg-file-header-reviewed pg-commit-msg-reviewed-label" title="Mark /COMMIT_MSG as reviewed (Shortcut: f)" onclick="event.stopPropagation()">
            <input type="checkbox" class="pg-diff-header-reviewed-cb" data-cl="${c.number}" data-ps="${curPs}" data-file="/COMMIT_MSG" ${isCommitMsgRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this)">
            <span>Reviewed</span>
          </label>
        </div>
      </div>
      <div class="commit-msg-body code-table commit-msg-table scheme-${escapeHtml(scheme)}" data-cl="${c.number}" data-file="/COMMIT_MSG">
        ${formattedFullMsgHtml}
        <div class="commit-msg-fade-overlay" aria-hidden="true"></div>
      </div>
      <div id="commitMsgEditor-${c.number}" class="pg-commit-msg-editor" style="${isEditing ? "display:flex;" : "display:none;"}">
        <div class="pg-commit-editor-toolbar">
          <div class="pg-commit-editor-title-wrap">
            <span class="pg-commit-editor-title"><span class="pg-btn-icon" aria-hidden="true">✏️</span> Edit Commit Message</span>
            <span class="pg-commit-editor-meta">ag/${c.number} · PS ${curPs}</span>
            <span id="commitMsgDirtyBadge-${c.number}" class="pg-editor-dirty-badge" style="display:none;">Unsaved changes</span>
          </div>
          <div class="pg-commit-editor-mode-toggle" role="tablist" aria-label="Editor view mode">
            <button type="button" id="commitMsgModeBtn-write-${c.number}" class="pg-commit-mode-btn ${editorMode === "write" ? "is-active" : ""}" role="tab" aria-selected="${editorMode === "write" ? "true" : "false"}" onclick="setPgCommitMsgEditorMode(${c.number}, 'write')" title="Write / Edit commit message (raw text)"><span class="pg-btn-icon" aria-hidden="true">✍️</span> Write</button>
            <button type="button" id="commitMsgModeBtn-preview-${c.number}" class="pg-commit-mode-btn ${editorMode === "preview" ? "is-active" : ""}" role="tab" aria-selected="${editorMode === "preview" ? "true" : "false"}" onclick="setPgCommitMsgEditorMode(${c.number}, 'preview')" title="Rendered Markdown preview with bug/issue and footer links"><span class="pg-btn-icon" aria-hidden="true">👁️</span> Preview</button>
            <button type="button" id="commitMsgModeBtn-split-${c.number}" class="pg-commit-mode-btn ${editorMode === "split" ? "is-active" : ""}" role="tab" aria-selected="${editorMode === "split" ? "true" : "false"}" onclick="setPgCommitMsgEditorMode(${c.number}, 'split')" title="Side-by-side editor and live preview"><span class="pg-btn-icon" aria-hidden="true">◫</span> Split</button>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            <button type="button" class="btn pg-btn-subtle pg-btn-commit-wrap" onclick="wrapPgCommitMsgText(${c.number})" title="Intelligently wrap body lines at <= 72 characters while preserving subject and footers">Wrap at 72</button>
            <button type="button" class="btn pg-btn-subtle pg-btn-commit-revert" onclick="revertPgCommitMsgText(${c.number})" title="Revert to original message">Revert</button>
          </div>
        </div>
        <div id="commitMsgEditorContent-${c.number}" class="pg-commit-editor-content mode-${escapeHtml(editorMode)}">
          <div class="pg-commit-textarea-wrap" id="commitMsgTextareaWrap-${c.number}">
            <textarea
              id="commitMsgTextarea-${c.number}"
              class="pg-commit-textarea"
              rows="12"
              spellcheck="false"
              placeholder="Type commit message..."
              oninput="onPgCommitMsgInput(${c.number}, this)"
              onkeydown="onPgCommitMsgKeyDown(event, ${c.number})"
            >${escapeHtml(draftText)}</textarea>
          </div>
          <div class="pg-commit-preview-wrap" id="commitMsgPreviewWrap-${c.number}">
            <div class="pg-commit-preview-header">
              <span class="pg-commit-preview-title">Rendered Preview</span>
              <span class="pg-commit-preview-badge">Live Markdown &amp; Git Trailers</span>
            </div>
            <div id="commitMsgPreviewPane-${c.number}" class="pg-commit-preview-pane commit-msg-body code-table commit-msg-table scheme-${escapeHtml(scheme)}" data-cl="${c.number}" data-file="/COMMIT_MSG">
              ${formattedDraftMsgHtml}
            </div>
          </div>
        </div>
        <div id="commitMsgMetricsBar-${c.number}" class="pg-commit-metrics-bar">
          ${metricsHtml}
        </div>
        <div class="pg-commit-editor-footer">
          <div class="pg-commit-editor-hint">
            <span><kbd>Ctrl</kbd>+<kbd>Enter</kbd> to save · <kbd>Esc</kbd> to cancel · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> to toggle preview</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <button type="button" id="btnCancelCommitMsg-${c.number}" class="btn pg-btn-subtle pg-btn-cancel-commit-msg" onclick="cancelPgCommitMsgEditor(${c.number})">Cancel</button>
            <button type="button" id="btnSaveCommitMsg-${c.number}" class="btn btn-primary pg-btn-save-commit-msg" onclick="savePgCommitMsgEditor(${c.number})">Save</button>
          </div>
        </div>
      </div>
      ${expandBarHtml}
    </div>`;
    if (canCacheCommitMsg) {
      c._cachedCommitMsgPanelKey = commitMsgCacheKey;
      c._cachedCommitMsgPanelHtml = panelHtml;
      const wSub = c._cmPanSub || (c._cmPanSub = {});
      wSub.num = c.number;
      wSub.ps = curPs;
      wSub.col = isCollapsed;
      wSub.exp = isExpanded;
      wSub.scheme = scheme;
      wSub.rev = isCommitMsgRev;
      wSub.msg = fullMsg;
    }
    return panelHtml;
  }

  function selectFullCommitMessage(clNum, mode = "comment") {
    const num = Number(clNum);
    uncollapseCommitMessage(num, true);
    const tableEl = document.querySelector(`.commit-msg-table[data-cl="${num}"]`);
    if (!tableEl) return;
    const allLines = tableEl.querySelectorAll(".code-line[data-idx]");
    if (!allLines.length) return;
    const prev = state.codeSelection;
    if (prev && Number(prev.clNum) === num && prev.filePath === "/COMMIT_MSG") {
      setCodeSelectionMode(mode);
      return;
    }
    selectCodeBlockRange(tableEl, 0, allLines.length - 1, {
      anchorIdx: 0,
      mode,
      focusInput: true,
    });
  }

  function onPgCommitMsgBarClick(event, clNum) {
    if (event && event.target && event.target.closest("a, input, button, label, select, textarea")) {
      return;
    }
    toggleCommitMessage(clNum);
  }

  function toggleCommitMessage(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const ps = Number(selPsMeta.ps || 1);
    const wasCollapsed = isPgFileCollapsed(num, ps, "/COMMIT_MSG");
    const nextCollapsed = !wasCollapsed;
    setPgFileCollapsed(num, ps, "/COMMIT_MSG", nextCollapsed);
    if (typeof showToast === "function") {
      showToast(nextCollapsed ? "Collapsed commit message (Shortcut: z or i)" : "Expanded commit message (Shortcut: z or i)");
    }
  }

  function toggleCommitMessageExpand(clNum) {
    const num = Number(clNum);
    if (!state.expandedCommitMsgs) state.expandedCommitMsgs = new Set();
    if (!state.collapsedCommitMsgs) state.collapsedCommitMsgs = new Set();

    if (state.collapsedCommitMsgs.has(num)) {
      state.collapsedCommitMsgs.delete(num);
      const panel = document.getElementById(`commitMsgPanel-${num}`);
      const btn = document.getElementById(`commitMsgToggleBtn-${num}`);
      if (panel) panel.classList.remove("collapsed");
      if (btn) btn.textContent = "Hide ▴";
    }

    const wasExpanded = state.expandedCommitMsgs.has(num);
    if (wasExpanded) {
      state.expandedCommitMsgs.delete(num);
    } else {
      state.expandedCommitMsgs.add(num);
    }
    const isNowExpanded = !wasExpanded;

    const panel = document.getElementById(`commitMsgPanel-${num}`);
    const expandBtn = document.getElementById(`btnCommitMsgExpand-${num}`);
    const expandText = document.getElementById(`btnCommitMsgExpandText-${num}`);
    const expandIcon = expandBtn ? expandBtn.querySelector(".commit-msg-expand-icon") : null;
    const lines = panel ? Number(panel.getAttribute("data-lines") || 0) : 0;

    if (panel) {
      panel.classList.toggle("is-expanded", isNowExpanded);
      panel.classList.toggle("is-preview", !isNowExpanded);
    }
    if (expandBtn) {
      expandBtn.classList.toggle("is-expanded", isNowExpanded);
      expandBtn.title = isNowExpanded ? "Collapse to preview (Shortcut: z)" : "Show all lines (Shortcut: z)";
    }
    if (expandText) {
      expandText.textContent = isNowExpanded ? "Show less (z)" : `Show all ${lines || "all"} lines (z)`;
    }
    if (expandIcon) {
      expandIcon.textContent = isNowExpanded ? "▴" : "▾";
    }
    if (typeof showToast === "function") {
      showToast(isNowExpanded ? `✓ Expanded commit message (${lines} lines)` : "Collapsed commit message to preview");
    }
  }

  function togglePgCommitMessage(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const activeFile = (state.activeFileByCl && state.activeFileByCl[num]) ||
                       (state.focusedFileByCl && state.focusedFileByCl[num]) || "";
    if (activeFile === "/MERGE_LIST") {
      toggleMergeList(num);
      return;
    }
    const panel = document.getElementById(`commitMsgPanel-${num}`);
    const isCollapsed = Boolean(state.collapsedCommitMsgs && state.collapsedCommitMsgs.has(num));
    const isLong = panel ? Number(panel.getAttribute("data-lines") || 0) > COMMIT_MSG_PREVIEW_LINE_LIMIT : false;

    if (isCollapsed) {
      toggleCommitMessage(num);
    } else if (isLong) {
      toggleCommitMessageExpand(num);
    } else {
      toggleCommitMessage(num);
    }
  }

  function copyCommitMessage(clNum, btnEl) {
    const num = Number(clNum);
    const c = (state.changes || []).find(x => Number(x.number) === num);
    const bundle = (state.diffsByCl && state.diffsByCl[num]) || null;
    const msg = getFullCommitMessage(c, bundle);
    if (!msg) return;
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(msg);
    }
    const btn = btnEl || document.getElementById(`btnCopyCommitMsg-${num}`);
    if (btn) {
      const origText = btn.innerHTML;
      btn.innerHTML = "✓ Copied";
      btn.classList.add("is-copied");
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.classList.remove("is-copied");
      }, 1800);
    }
    if (typeof showToast === "function") {
      showToast(`✓ Copied full commit message for ag/${num}!`);
    }
  }

  function isPgMergeCommit(c, bundle) {
    if (!c && !bundle) return false;
    if (c && c.is_merge_commit) return true;
    if (bundle && bundle.is_merge_commit) return true;
    if (c && Array.isArray(c.parents) && c.parents.length > 1) return true;
    if (bundle && Array.isArray(bundle.parents) && bundle.parents.length > 1) return true;
    if (c && Array.isArray(c.merge_list) && c.merge_list.length > 0) return true;
    if (bundle && Array.isArray(bundle.merge_list) && bundle.merge_list.length > 0) return true;
    if (bundle && Array.isArray(bundle.files)) {
      if (
        bundle._cachedMergeListFilesLen !== bundle.files.length ||
        bundle._cachedMergeListFirstFile !== bundle.files[0]
      ) {
        bundle._cachedMergeListFilesLen = bundle.files.length;
        bundle._cachedMergeListFirstFile = bundle.files[0];
        bundle._cachedHasMergeListFile = bundle.files.some(f => f && f.file === "/MERGE_LIST");
      }
      if (bundle._cachedHasMergeListFile) return true;
    }
    return false;
  }

  function getPgMergeList(c, bundle) {
    if (bundle && Array.isArray(bundle.merge_list) && bundle.merge_list.length > 0) {
      return bundle.merge_list;
    }
    if (c && Array.isArray(c.merge_list) && c.merge_list.length > 0) {
      return c.merge_list;
    }
    if (c && Array.isArray(c.parents) && c.parents.length > 1) {
      return c.parents.slice(1).map((p, idx) => {
        if (typeof p === "object" && p !== null) {
          return {
            commit: p.commit || p.sha || `merge-parent-${idx + 2}`,
            author: p.author || { name: p.author_name || "Merged Author" },
            date: p.date || p.created || "",
            subject: p.subject || p.message || `Merged parent commit ${idx + 2}`,
          };
        }
        return {
          commit: String(p),
          author: { name: "Merged Author" },
          date: "",
          subject: `Merged parent ${String(p).slice(0, 8)}`,
        };
      });
    }
    if (bundle && Array.isArray(bundle.parents) && bundle.parents.length > 1) {
      return bundle.parents.slice(1).map((p, idx) => ({
        commit: typeof p === "object" && p ? (p.commit || p.sha || `merge-parent-${idx + 2}`) : String(p),
        author: typeof p === "object" && p && p.author ? p.author : { name: "Merged Author" },
        date: typeof p === "object" && p && p.date ? p.date : "",
        subject: typeof p === "object" && p && p.subject ? p.subject : `Merged parent ${String(p).slice(0, 8)}`,
      }));
    }
    return [];
  }

  function isMergeListCollapsed(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!state.collapsedMergeLists) state.collapsedMergeLists = new Set();
    return state.collapsedMergeLists.has(num);
  }

  function onPgMergeListBarClick(event, clNum) {
    if (event && event.target && event.target.closest("a, input, button, label, select, textarea")) {
      return;
    }
    toggleMergeList(clNum);
  }

  function toggleMergeList(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const ps = Number(selPsMeta.ps || 1);
    const wasCollapsed = isPgFileCollapsed(num, ps, "/MERGE_LIST");
    const nextCollapsed = !wasCollapsed;
    setPgFileCollapsed(num, ps, "/MERGE_LIST", nextCollapsed);
    if (typeof showToast === "function") {
      showToast(nextCollapsed ? "Collapsed merge list (Shortcut: z or i)" : "Expanded merge list (Shortcut: z or i)");
    }
  }

  function uncollapseMergeList(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const ps = Number(selPsMeta.ps || 1);
    setPgFileCollapsed(num, ps, "/MERGE_LIST", false);
  }

  function scrollToMergeListBox(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    updatePgActiveFile(num, "/MERGE_LIST");
    uncollapseMergeList(num);
    const box = document.getElementById(`mergeListPanel-${num}`) ||
                document.getElementById(`mergeListBox-${num}`);
    if (box && typeof box.scrollIntoView === "function") {
      box.scrollIntoView({ behavior: "smooth", block: "start" });
      box.classList.remove("pg-diff-focused-flash");
      void box.offsetWidth;
      box.classList.add("pg-diff-focused-flash");
      setTimeout(() => {
        box.classList.remove("pg-diff-focused-flash");
      }, 1400);
    }
  }

  function copyMergeListShas(clNum, btnEl) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const c = findChangeByNumber(num);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const mergeCommits = getPgMergeList(c, bundle);
    const shas = mergeCommits.map(mc => (mc.commit || mc.sha || "").trim()).filter(Boolean);
    const text = shas.join("\n");
    if (!text) {
      if (typeof showToast === "function") showToast("No commit SHAs in merge list to copy");
      return;
    }
    if (typeof copyPgText === "function") {
      copyPgText(text, `${shas.length} merged commit SHA(s)`);
    } else if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(text);
    }
    const btn = btnEl || document.getElementById(`btnCopyMergeList-${num}`);
    if (btn) {
      const origText = btn.innerHTML;
      btn.innerHTML = "✓ Copied";
      btn.classList.add("is-copied");
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.classList.remove("is-copied");
      }, 1800);
    }
    if (typeof showToast === "function") {
      showToast(`✓ Copied ${shas.length} merged commit SHA(s) to clipboard!`);
    }
  }

  function selectFullMergeList(clNum, mode = "comment") {
    const num = Number(clNum || state.selectedClNumber || 0);
    uncollapseMergeList(num);
    const tableEl = document.querySelector(`.merge-list-table[data-cl="${num}"]`);
    if (!tableEl) return;
    const allLines = tableEl.querySelectorAll(".code-line[data-idx]");
    if (!allLines.length) return;
    const prev = state.codeSelection;
    if (prev && Number(prev.clNum) === num && prev.filePath === "/MERGE_LIST") {
      setCodeSelectionMode(mode);
      return;
    }
    selectCodeBlockRange(tableEl, 0, allLines.length - 1, {
      anchorIdx: 0,
      mode,
      focusInput: true,
    });
  }

  function renderMergeListPanelHtml(c, bundle) {
    if (!c || !isPgMergeCommit(c, bundle)) return "";
    const mergeCommits = getPgMergeList(c, bundle);
    const totalCommits = mergeCommits.length;
    const isCollapsed = isMergeListCollapsed(c.number);
    const scheme = state.diffColorScheme || "polygerrit_native";
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[c.number]) || {};
    const curPs = Number(selPsMeta.ps || (bundle && bundle.patch_set) || c.patch_set || 1);

    const commitsMetaHtml = totalCommits > 0
      ? `<span class="commit-msg-meta" title="${totalCommits} merged commits in merge list">· ${totalCommits} commit${totalCommits === 1 ? "" : "s"}</span>`
      : "";

    let commitsRowsHtml = "";
    if (totalCommits === 0) {
      commitsRowsHtml = `<div class="merge-list-empty" style="padding:12px;color:var(--g-muted);font-style:italic;">No merged commit details available for this merge commit.</div>`;
    } else {
      commitsRowsHtml = mergeCommits.map((mc, idx) => {
        const sha = (mc.commit || mc.sha || "").trim();
        const shortSha = sha ? sha.slice(0, 8) : `commit-${idx + 1}`;
        const author = typeof mc.author === "object" && mc.author ? (mc.author.name || mc.author.email || "") : String(mc.author || "");
        const date = (mc.date || "").trim();
        const subject = (mc.subject || mc.message || "").trim();
        const safeSubject = escapeHtml(subject);
        const safeAuthor = escapeHtml(author);
        const safeSha = escapeHtml(shortSha);
        const safeFullSha = escapeHtml(sha);

        return `<div class="code-line merge-list-line" data-idx="${idx}" data-line="${idx + 1}" data-sha="${safeFullSha}">
          <span class="line-num line-num-merge" data-line="${idx + 1}">${idx + 1}</span>
          <div class="line-content merge-commit-content">
            <span class="merge-commit-sha" title="${safeFullSha || safeSha}" onclick="copyPgText('${safeFullSha || safeSha}', 'commit SHA')">${safeSha}</span>
            ${author ? `<span class="merge-commit-author" title="${safeAuthor}">${safeAuthor}</span>` : ""}
            ${date ? `<span class="merge-commit-date" title="${escapeHtml(date)}">${escapeHtml(date)}</span>` : ""}
            <span class="merge-commit-subject" title="${safeSubject}">${safeSubject || "<em>(No subject)</em>"}</span>
          </div>
        </div>`;
      }).join("");
    }

    const isMergeListRev = (typeof isPgFileReviewed === "function") && isPgFileReviewed(c.number, curPs, "/MERGE_LIST");
    return `<div id="mergeListPanel-${c.number}" class="commit-msg-panel merge-list-panel ${isCollapsed ? "collapsed is-collapsed" : ""}" data-cl="${c.number}" data-file="/MERGE_LIST" data-ps="${curPs}" data-commits="${totalCommits}">
      <div id="mergeListBox-${c.number}" style="display:none;" aria-hidden="true"></div>
      <div class="commit-msg-bar merge-list-bar" onclick="onPgMergeListBarClick(event, ${c.number})" title="Click to toggle merge list visibility (Shortcut: z or i)">
        <div class="pg-commit-msg-bar-left" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <button type="button" class="pg-file-diff-toggle-btn pg-merge-list-toggle-btn" title="Toggle merge list visibility (Shortcut: z or i)" onclick="event.stopPropagation(); toggleMergeList(${c.number})">
            <span class="pg-file-chevron">${isCollapsed ? "▶" : "▼"}</span>
          </button>
          <span class="pg-file-status-badge status-M" title="Merge List">M</span>
          <span class="commit-msg-title">🔀 Merge List</span>
          ${commitsMetaHtml}
          <span class="file-header-hint">Commits merged into target branch</span>
        </div>
        <div class="commit-msg-actions">
          <div class="pg-diff-nav-group pg-commit-msg-nav-group">
            <button type="button" class="pg-btn-diff-nav btn-chunk-next" onclick="event.stopPropagation(); navigatePgDiffChunk('next')" title="Next diff chunk or comment thread (Shortcut: n)">Chunk (n) <span aria-hidden="true">►</span></button>
            <button type="button" class="pg-btn-diff-nav btn-diff-next" onclick="event.stopPropagation(); navigatePgFileDiff('next', '/MERGE_LIST', ${c.number})" title="Next file diff"><span aria-hidden="true">▼</span> Next</button>
            <button type="button" class="pg-btn-diff-nav btn-diff-up" onclick="event.stopPropagation(); scrollToPgFileList(${c.number})" title="Jump up to file list (Shortcut: u)"><span aria-hidden="true">↑</span> File list</button>
          </div>
          <button class="commit-msg-btn" onclick="event.stopPropagation(); selectFullMergeList(${c.number}, 'comment')">💬 Comment</button>
          <button class="commit-msg-btn" onclick="event.stopPropagation(); selectFullMergeList(${c.number}, 'ask')">🤖 Ask Agent</button>
          <button id="btnCopyMergeList-${c.number}" class="commit-msg-btn" onclick="event.stopPropagation(); copyMergeListShas(${c.number}, this)" title="Copy merged commit SHAs">📋 Copy SHAs</button>
          <button id="mergeListToggleBtn-${c.number}" class="commit-msg-btn" onclick="event.stopPropagation(); toggleMergeList(${c.number})" title="Toggle merge list visibility (Shortcut: z or i)">${isCollapsed ? "Show ▾" : "Hide ▴"}</button>
          <label class="pg-file-header-reviewed pg-commit-msg-reviewed-label" title="Mark /MERGE_LIST as reviewed (Shortcut: f)" onclick="event.stopPropagation()">
            <input type="checkbox" class="pg-diff-header-reviewed-cb" data-cl="${c.number}" data-ps="${curPs}" data-file="/MERGE_LIST" ${isMergeListRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this)">
            <span>Reviewed</span>
          </label>
        </div>
      </div>
      <div class="commit-msg-body code-table merge-list-table scheme-${escapeHtml(scheme)}" data-cl="${c.number}" data-file="/MERGE_LIST">
        ${commitsRowsHtml}
      </div>
    </div>`;
  }

  function computeSingleCommitDiffRenderSig(c) {
    const b = state.diffsByCl[c.number];
    const hasFiles = Boolean(b && Array.isArray(b.files));
    const threadCnt = hasFiles && Array.isArray(b.unresolved_threads) ? b.unresolved_threads.length : -1;
    const subTarget = computeCommitSubmitTarget(c);
    if (state._inRenderDiffPane) {
      c._renderPassSubmitTarget = subTarget;
    }
    const aiEntry = state.aiAnalysisByCl[c.number];
    const aiStatus = (aiEntry && aiEntry.analysis_status !== "not_analyzed" && !state.hiddenAiAnalysisCls.has(Number(c.number)))
      ? aiEntry.analysis_status
      : "";
    const bugsArr = Array.isArray(c.bugs) ? c.bugs : null;
    const bugsLen = bugsArr ? bugsArr.length : 0;
    const revsArr = Array.isArray(c.reviewers_detail) ? c.reviewers_detail : null;
    const revsLen = revsArr ? revsArr.length : 0;
    const r0Vote = (revsArr && revsArr[0]) ? revsArr[0].cr_vote : 0;
    const r0VotesLen = (revsArr && revsArr[0] && Array.isArray(revsArr[0].votes)) ? revsArr[0].votes.length : 0;
    const ccCnt = Array.isArray(c.ccs_detail) ? c.ccs_detail.length : 0;
    const dsSub = c._diffSigSub;
    if (
      dsSub &&
      dsSub.sig &&
      dsSub.num === c.number &&
      dsSub.ps === c.patch_set &&
      dsSub.hasFiles === hasFiles &&
      dsSub.threadCnt === threadCnt &&
      dsSub.vCr === c.viewer_cr_vote &&
      dsSub.vAs === c.viewer_autosubmit &&
      dsSub.vPr === c.viewer_presubmit_ready &&
      dsSub.pvStatus === c.presubmit_status &&
      dsSub.wip === c.work_in_progress &&
      dsSub.mergeable === c.mergeable &&
      dsSub.subCan === subTarget.canSubmit &&
      dsSub.subLbl === subTarget.label &&
      dsSub.bugsRef === bugsArr &&
      dsSub.bugsLen === bugsLen &&
      dsSub.revsRef === revsArr &&
      dsSub.revsLen === revsLen &&
      dsSub.r0Vote === r0Vote &&
      dsSub.r0VotesLen === r0VotesLen &&
      dsSub.ccCnt === ccCnt &&
      dsSub.aiStatus === aiStatus
    ) {
      return dsSub.sig;
    }
    const bugStr = bugsArr ? bugsArr.map(x => `${x.id}:${x.priority || ""}`).join(",") : "";
    const revStr = revsArr ? revsArr.map(p => `${p.email}:${p.cr_vote}:${(p.votes || []).length}`).join(",") : "";
    const aiPart = aiStatus ? `|ai:${aiStatus}` : "";
    const sig = `${c.number}@${c.patch_set}|${hasFiles ? 1 : 0}|${threadCnt}|${c.viewer_cr_vote}|${c.viewer_autosubmit ? 1 : 0}|${c.viewer_presubmit_ready ? 1 : 0}|${c.presubmit_status}|${c.work_in_progress ? 1 : 0}|${c.mergeable === false ? 0 : 1}|${subTarget.canSubmit ? 1 : 0}:${subTarget.label}|${bugStr}|${revStr}|${ccCnt}${aiPart}`;
    const wSub = c._diffSigSub || (c._diffSigSub = {});
    wSub.num = c.number;
    wSub.ps = c.patch_set;
    wSub.hasFiles = hasFiles;
    wSub.threadCnt = threadCnt;
    wSub.vCr = c.viewer_cr_vote;
    wSub.vAs = c.viewer_autosubmit;
    wSub.vPr = c.viewer_presubmit_ready;
    wSub.pvStatus = c.presubmit_status;
    wSub.wip = c.work_in_progress;
    wSub.mergeable = c.mergeable;
    wSub.subCan = subTarget.canSubmit;
    wSub.subLbl = subTarget.label;
    wSub.bugsRef = bugsArr;
    wSub.bugsLen = bugsLen;
    wSub.revsRef = revsArr;
    wSub.revsLen = revsLen;
    wSub.r0Vote = r0Vote;
    wSub.r0VotesLen = r0VotesLen;
    wSub.ccCnt = ccCnt;
    wSub.aiStatus = aiStatus;
    wSub.sig = sig;
    return sig;
  }

  function computeDiffRenderSignature(commits) {
    if (state.checkedClNumbers && state.checkedClNumbers.size > 0) {
      const checkedNums = getOrderedCheckedClNumbers();
      if (checkedNums.length > 0) {
        const checkedParts = checkedNums.map(num => {
          const c = state.changes.find(x => Number(x.number) === Number(num));
          if (!c) return String(num);
          const revStr = (c.reviewers_detail || []).map(p => `${p.email}:${p.cr_vote}`).join(",");
          const ccCnt = (c.ccs_detail || []).length;
          return `${c.number}@${c.patch_set}|${c.viewer_cr_vote}|${c.viewer_autosubmit ? 1 : 0}|${c.viewer_presubmit_ready ? 1 : 0}|${c.presubmit_status}|${isClSubmittable(c) ? 1 : 0}|${revStr}|${ccCnt}`;
        });
        return `${state.viewer}|batch|${checkedParts.join(";;")}`;
      }
    }
    if (!commits || commits.length === 0) {
      return `empty:${state.queueClearedCount}`;
    }
    let partsStr = computeSingleCommitDiffRenderSig(commits[0]);
    for (let i = 1; i < commits.length; i++) {
      partsStr += ";;" + computeSingleCommitDiffRenderSig(commits[i]);
    }
    return `${state.viewer}|${state.diffColorScheme}|${state.continuousStackDiff ? "stack" : "single"}:${state.selectedClNumber || 0}|${partsStr}`;
  }

  function renderExpandSidebarButtonHtml() {
    /* id="btnExpandSidebar" */
    const isStacked = typeof window !== "undefined" && (window.innerWidth || 1200) <= 720;
    const visible = Boolean(state.sidebarCollapsed || isStacked);
    return AemuGerritReact.renderExpandSidebarButtonHtml(visible, Number(state.visibleCommitCount || 0));
  }

  function renderCommitNavButtonsHtml() {
    const disabledAttr = canNavigateVisibleCls() ? "" : " disabled";
    return `<div class="cl-nav-group" role="group" aria-label="Navigate visible CLs">
      <button
        id="btnNavPrev"
        type="button"
        class="btn btn-nav-cl"${disabledAttr}
        onclick="event.stopPropagation(); navigateVisibleCl('prev')"
        title="Previous visible CL without voting (Shortcut: k)"
        aria-label="Previous visible CL (Shortcut: k)"
      >← Prev (k)</button>
      <button
        id="btnNavNext"
        type="button"
        class="btn btn-nav-cl"${disabledAttr}
        onclick="event.stopPropagation(); navigateVisibleCl('next')"
        title="Next visible CL without voting (Shortcut: j)"
        aria-label="Next visible CL (Shortcut: j)"
      >Next (j) →</button>
    </div>`;
  }

  const AEMU_MAP_BASE_URL = "http://go/aemu-map";

  function buildAemuMapCommitUrl(c, bundle = null) {
    if (!c || !c.number) return AEMU_MAP_BASE_URL;
    const bFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : null;
    const cFiles = Array.isArray(c.files) ? c.files : null;
    const bLen = bFiles ? bFiles.length : -1;
    const cLen = cFiles ? cFiles.length : -1;
    const firstFile = (bFiles && bFiles[0] && (bFiles[0].file || bFiles[0])) || (cFiles && cFiles[0] && (cFiles[0].file || cFiles[0])) || "";
    const mSub = c._mapSub;
    if (
      mSub &&
      typeof c._cachedAemuMapUrl === "string" &&
      mSub.num === c.number &&
      mSub.bLen === bLen &&
      mSub.cLen === cLen &&
      mSub.firstFile === firstFile &&
      mSub.mapUrl === c.aemu_map_url
    ) {
      return c._cachedAemuMapUrl;
    }
    const mapCacheKey = `${c.number}|${bLen}|${cLen}|${firstFile}|${c.aemu_map_url || ""}`;
    if (c._cachedAemuMapKey === mapCacheKey && typeof c._cachedAemuMapUrl === "string") {
      const wSub = c._mapSub || (c._mapSub = {});
      wSub.num = c.number;
      wSub.bLen = bLen;
      wSub.cLen = cLen;
      wSub.firstFile = firstFile;
      wSub.mapUrl = c.aemu_map_url;
      return c._cachedAemuMapUrl;
    }
    const rawFiles = (bFiles && bFiles.length > 0)
      ? bFiles.map(f => f && f.file)
      : (cFiles || []);
    const cleaned = [];
    const seen = new Set();
    for (const item of rawFiles) {
      const raw = String(item || "").trim();
      if (!raw || (typeof GERRIT_MAGIC_FILES !== "undefined" && GERRIT_MAGIC_FILES.has(raw))) continue;
      const rel = raw.replace(/^\/+/, "");
      if (!rel || rel === "dev/null" || (typeof GERRIT_MAGIC_FILES !== "undefined" && (GERRIT_MAGIC_FILES.has(rel) || GERRIT_MAGIC_FILES.has("/" + rel)))) continue;
      if (!seen.has(rel)) {
        seen.add(rel);
        cleaned.push(rel);
      }
    }
    let resultUrl;
    if (cleaned.length === 0 && c.aemu_map_url) {
      resultUrl = c.aemu_map_url;
    } else {
      const encodedCl = encodeURIComponent(String(c.number));
      if (cleaned.length === 0) {
        resultUrl = `${AEMU_MAP_BASE_URL}#ag=${encodedCl}`;
      } else {
        const encodedFiles = cleaned.map(p => _ASCII_REL_PATH_RE.test(p) ? p : p.split("/").map(seg => encodeURIComponent(seg)).join("/")).join(",");
        resultUrl = `${AEMU_MAP_BASE_URL}#ag=${encodedCl}&files=${encodedFiles}`;
      }
    }
    c._cachedAemuMapKey = mapCacheKey;
    c._cachedAemuMapUrl = resultUrl;
    const wSub = c._mapSub || (c._mapSub = {});
    wSub.num = c.number;
    wSub.bLen = bLen;
    wSub.cLen = cLen;
    wSub.firstFile = firstFile;
    wSub.mapUrl = c.aemu_map_url;
    return resultUrl;
  }

  const _curCommitRenderPass = {
    active: false,
    clNum: 0,
    refBundle: undefined,
    refInfo: null,
    mapBundle: undefined,
    mapUrl: "",
  };
  let _nextCommitSectionRenderSeq = 1;

  function renderCommitAemuMapButtonHtml(c, bundle = null) {
    if (!c || !c.number) return "";
    const clNum = Number(c.number);
    const b = bundle || (typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[clNum]) || null;
    const usePass = _curCommitRenderPass.active && _curCommitRenderPass.clNum === clNum && _curCommitRenderPass.mapBundle === b && _curCommitRenderPass.mapUrl;
    const aemuMapUrl = (c._loopAemuMapBundle === b && c._loopAemuMapUrl) ||
      (usePass ? _curCommitRenderPass.mapUrl : buildAemuMapCommitUrl(c, b));
    if (_curCommitRenderPass.active && _curCommitRenderPass.clNum === clNum) {
      _curCommitRenderPass.mapBundle = b;
      _curCommitRenderPass.mapUrl = aemuMapUrl;
    }
    return AemuGerritReact.renderCommitAemuMapButtonHtml(clNum, aemuMapUrl);
  }

  function syncAemuMapButtons(commits) {
    const list = Array.isArray(commits) && commits.length > 0 ? commits : (typeof getActiveCommitsForDiff === "function" ? getActiveCommitsForDiff() : []);
    if (list.length === 0) return;
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      if (!c || !c.number) continue;
      const num = Number(c.number);
      const bundle = (typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[num]) || null;
      const url = buildAemuMapCommitUrl(c, bundle);
      const btn = document.getElementById(`btnArchitectureMap-${num}`);
      if (btn && btn._lastMapHref !== url) {
        btn._lastMapHref = url;
        btn.href = url;
      }
    }
    const selNum = typeof state !== "undefined" && state ? Number(state.selectedClNumber) : 0;
    let primary = list[0];
    for (let i = 0; i < list.length; i++) {
      if (list[i] && Number(list[i].number) === selNum) {
        primary = list[i];
        break;
      }
    }
    if (primary) {
      const openAemuMapEl = document.getElementById("openAemuMapLink");
      if (openAemuMapEl) {
        const bundle = (typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[primary.number]) || null;
        const primaryUrl = buildAemuMapCommitUrl(primary, bundle);
        if (openAemuMapEl._lastMapHref !== primaryUrl) {
          openAemuMapEl._lastMapHref = primaryUrl;
          openAemuMapEl.href = primaryUrl;
        }
      }
    }
  }

  function renderCommitCodeViewerButtonHtml(c, bundle = null) {
    const num = Number(c && c.number);
    if (!num) return "";
    const b = bundle || (typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[num]) || null;
    const fCount = (b && Array.isArray(b.files))
      ? b.files.length
      : (Array.isArray(c && c.files) ? c.files.length : 0);
    return AemuGerritReact.renderPgCodeViewerToggleButtonHtml({
      clNumber: num,
      isActive: Boolean(state.codeViewerMode),
      fileCount: fCount,
    });
  }

  function renderCommitHarnessActionsHtml(c, bundle = null) {
    const num = Number(c && c.number);
    const b = bundle || (typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[num]) || null;
    const codeViewerBtnHtml = renderCommitCodeViewerButtonHtml(c, b);
    const aemuMapBtnHtml = renderCommitAemuMapButtonHtml(c, b);
    const navBtnsHtml = renderCommitNavButtonsHtml();
    return `<div class="harness-group">
      ${navBtnsHtml}
      <span id="activeSwarmsPill" class="active-swarms-pill" style="display:none;" onclick="jumpToActiveSwarmCl()"></span>
      ${codeViewerBtnHtml}
      ${aemuMapBtnHtml}
      <div id="harnessDropdownWrap" class="harness-dropdown-wrap" onclick="event.stopPropagation()">
        <button
          id="btnHarnessMenuToggle"
          type="button"
          class="btn btn-harness-menu"
          onclick="toggleHarnessMenu(event)"
          aria-haspopup="menu"
          aria-expanded="false"
          title="Open Harness Actions for ag/${c.number}"
        >
          <span id="harnessMenuLabel">⚡ Harness Actions</span>
          <span class="harness-menu-caret">▾</span>
        </button>
      </div>
    </div>`;
  }

  const _PG_HDR_STATUS_MERGED = Object.freeze({ cls: "status-merged", label: "Merged", tip: "Change has been submitted and merged" });
  const _PG_HDR_STATUS_ABANDONED = Object.freeze({ cls: "status-abandoned", label: "Abandoned", tip: "Change has been abandoned" });
  const _PG_HDR_STATUS_WIP = Object.freeze({ cls: "status-wip", label: "WIP", tip: "Work in progress" });
  const _PG_HDR_STATUS_READY = Object.freeze({ cls: "status-ready", label: "Ready to Submit", tip: "All submit requirements and checks satisfied" });
  const _PG_HDR_STATUS_ACTIVE = Object.freeze({ cls: "status-active", label: "Active", tip: "Active change awaiting review or presubmit" });

  function computePgHeaderStatusInfo(c, bundle = null) {
    if (isClMerged(c)) {
      return _PG_HDR_STATUS_MERGED;
    }
    if (isClAbandoned(c)) {
      return _PG_HDR_STATUS_ABANDONED;
    }
    const num = Number((c && c.number) || (bundle && bundle.number) || 0);
    const hasConflict = isClConflict(c, bundle);
    if (hasConflict) {
      const cachedMerge = (num && typeof state !== "undefined" && state && state.mergeabilityByCl && state.mergeabilityByCl[num]) || null;
      const rawConflictList = (bundle && Array.isArray(bundle.conflicts) && bundle.conflicts.length > 0)
        ? bundle.conflicts
        : ((c && Array.isArray(c.conflicts) && c.conflicts.length > 0)
          ? c.conflicts
          : ((cachedMerge && Array.isArray(cachedMerge.conflicts) && cachedMerge.conflicts.length > 0) ? cachedMerge.conflicts : []));
      const conflictFiles = rawConflictList.length > 0
        ? ` (${rawConflictList.join(", ")})`
        : "";
      const res = {
        cls: "status-conflict",
        label: "Merge Conflict",
        tip: `Merge conflict detected against target branch${conflictFiles}. This change has merge conflicts. Rebase on the upstream branch (e.g. "git pull --rebase"). Upload a new patchset after resolving all merge conflicts.`,
        hasConflict: true,
        conflicts: rawConflictList,
      };
      if (c && c.work_in_progress) {
        res.secondaryCls = "status-wip";
        res.secondaryLabel = "WIP";
        res.secondaryTip = "Work in progress";
      }
      return res;
    }
    if (c && c.work_in_progress) {
      return _PG_HDR_STATUS_WIP;
    }
    if (c && isClSubmittable(c)) {
      return _PG_HDR_STATUS_READY;
    }
    return _PG_HDR_STATUS_ACTIVE;
  }

  function getPgChangeReferenceInfo(c, bundle = null) {
    if (!c) {
      return {
        changeId: "",
        changeIdShort: "",
        commitSha: "",
        commitShaShort: "",
        parentSha: "",
        parentShaShort: "",
        parentGitilesUrl: "",
        parentClNumber: null,
        parentClSubject: "",
        parents: [],
        gerritUrl: "",
        shortlink: "",
        titleAndUrl: "",
        markdown: "",
        gitilesUrl: "",
        codeSearchUrl: "",
        filePaths: [],
        filePathsText: "",
        cherryPickOfChange: null,
        cherryPickOfPatchSet: 1,
        cherryPickOfRef: "",
        revertOfChange: null,
        patchSet: 1,
        repoDownloadCmd: "",
        gitCheckoutCmd: "",
        gitCherryPickCmd: "",
        gitFormatPatchCmd: "",
        gitPullCmd: "",
      };
    }
    const num = Number(c.number);
    const selPsVal = (typeof state !== "undefined" && state && state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || "";
    const selPsStr = typeof selPsVal === "object" && selPsVal !== null ? `${selPsVal.ps || ""}:${selPsVal.base || ""}` : String(selPsVal);
    const commitMsgLen = c.commit_message ? c.commit_message.length : 0;
    const parentRev = c.parent_revision || c.parent_commit || c.parent_sha || "";
    const parentsLen = Array.isArray(c.parents) ? c.parents.length : 0;
    const cFilesLen = Array.isArray(c.files) ? c.files.length : 0;
    const bPs = bundle ? (bundle.patch_set || 0) : -1;
    const bSha = bundle ? (bundle.commit_sha || "") : "";
    const bParent = bundle ? (bundle.parent_sha || bundle.parent_short || "") : "";
    const bFilesLen = (bundle && Array.isArray(bundle.files)) ? bundle.files.length : -1;
    const dlScheme = (typeof state !== "undefined" && state && state.downloadScheme) || "repo";
    const changesLen = (typeof state !== "undefined" && state && Array.isArray(state.changes)) ? state.changes.length : 0;
    const refSub = c._refSub;
    if (
      refSub &&
      c._cachedReferenceInfo &&
      refSub.num === num &&
      refSub.subject === c.subject &&
      refSub.url === c.url &&
      refSub.changeId === c.change_id &&
      refSub.commitMsgLen === commitMsgLen &&
      refSub.curRev === c.current_revision &&
      refSub.project === c.project &&
      refSub.parentRev === parentRev &&
      refSub.parentsLen === parentsLen &&
      refSub.cFilesLen === cFilesLen &&
      refSub.cpChange === c.cherry_pick_of_change &&
      refSub.cpPs === c.cherry_pick_of_patch_set &&
      refSub.revertOf === c.revert_of &&
      refSub.ps === c.patch_set &&
      refSub.bPs === bPs &&
      refSub.bSha === bSha &&
      refSub.bParent === bParent &&
      refSub.bFilesLen === bFilesLen &&
      refSub.dlScheme === dlScheme &&
      refSub.selPsStr === selPsStr &&
      refSub.changesLen === changesLen
    ) {
      return c._cachedReferenceInfo;
    }
    const bundleRefSig = bundle
      ? `${bPs}:${bSha}:${bParent}:${bFilesLen}`
      : "null";
    const refCacheKey = `${num}|${c.subject || ""}|${c.url || ""}|${c.change_id || ""}|${commitMsgLen}|${c.current_revision || ""}|${c.project || ""}|${parentRev}|${parentsLen}|${cFilesLen}|${c.cherry_pick_of_change || 0}|${c.cherry_pick_of_patch_set || 0}|${c.revert_of || 0}|${c.patch_set || 0}|${bundleRefSig}|${dlScheme}|${selPsStr}|${changesLen}`;
    if (c._cachedReferenceInfoKey === refCacheKey && c._cachedReferenceInfo) {
      return c._cachedReferenceInfo;
    }
    const subject = String(c.subject || "").trim();
    const host = (typeof GERRIT_HTTP_HOST !== "undefined" && GERRIT_HTTP_HOST)
      ? GERRIT_HTTP_HOST
      : "https://googleplex-android-review.git.corp.google.com";
    const gerritUrl = c.url || `${host}/c/${num}`;
    const shortlink = `ag/${num}`;
    const titleAndUrl = `${shortlink}: ${subject} (${gerritUrl})`;
    const markdown = `[${shortlink}: ${subject}](${gerritUrl})`;

    let changeId = String(c.change_id || "").trim();
    if (!changeId && c.commit_message) {
      const m = String(c.commit_message).match(/Change-Id:\s*(I[0-9a-fA-F]{40})/);
      if (m) changeId = m[1];
    }
    const changeIdShort = changeId ? (changeId.length > 12 ? `${changeId.slice(0, 10)}…` : changeId) : "";

    let commitSha = String(c.current_revision || (bundle && bundle.commit_sha) || "").trim();
    if (!commitSha && Array.isArray(c.patchset_details) && c.patchset_details.length > 0) {
      const latest = c.patchset_details[c.patchset_details.length - 1];
      if (latest && latest.commit_sha) commitSha = String(latest.commit_sha).trim();
    }
    const commitShaShort = commitSha ? commitSha.slice(0, 8) : "";

    const project = String(c.project || "").trim();
    const gitilesHost = host.includes("googleplex")
      ? "https://googleplex-android.googlesource.com"
      : "https://android.googlesource.com";

    let gitilesUrl = "";
    if (project && commitSha) {
      gitilesUrl = `${gitilesHost}/${project}/+/${commitSha}`;
    }

    const rawParentsList = [];
    if (Array.isArray(c.parents) && c.parents.length > 0) {
      c.parents.forEach(p => {
        if (typeof p === "string" && p.trim()) rawParentsList.push(p.trim());
        else if (p && typeof p === "object" && p.commit) rawParentsList.push(String(p.commit).trim());
      });
    }
    if (rawParentsList.length === 0) {
      const fallbackParent = String(c.parent_revision || c.parent_commit || c.parent_sha || (bundle && (bundle.parent_sha || bundle.parent_short)) || "").trim();
      if (fallbackParent) rawParentsList.push(fallbackParent);
    }

    const parents = rawParentsList.map(sha => {
      const shaShort = sha.length > 8 ? sha.slice(0, 8) : sha;
      const pGitilesUrl = (project && sha) ? `${gitilesHost}/${project}/+/${sha}` : "";
      const pCl = (typeof findChangeByRevision === "function") ? findChangeByRevision(sha) : null;
      return {
        sha,
        shaShort,
        gitilesUrl: pGitilesUrl,
        clNumber: pCl ? Number(pCl.number) : null,
        clSubject: pCl ? (pCl.subject || "") : "",
      };
    });

    const primaryParent = parents.length > 0 ? parents[0] : null;
    const parentSha = primaryParent ? primaryParent.sha : "";
    const parentShaShort = primaryParent ? primaryParent.shaShort : "";
    const parentGitilesUrl = primaryParent ? primaryParent.gitilesUrl : "";
    const parentClNumber = primaryParent ? primaryParent.clNumber : null;
    const parentClSubject = primaryParent ? primaryParent.clSubject : "";

    const csQuery = changeId || String(num);
    const codeSearchUrl = `https://source.corp.google.com/search?q=${encodeURIComponent(csQuery)}`;

    const rawFiles = (bundle && Array.isArray(bundle.files) && bundle.files.length > 0)
      ? bundle.files.map(f => f && f.file)
      : (Array.isArray(c.files) ? c.files.map(f => (typeof f === "string" ? f : (f && f.file))) : []);
    const filePaths = [];
    const seenFiles = new Set();
    for (const item of rawFiles) {
      const raw = String(item || "").trim();
      if (!raw) continue;
      if (typeof GERRIT_MAGIC_FILES !== "undefined" && GERRIT_MAGIC_FILES.has(raw)) continue;
      const rel = raw.replace(/^\/+/, "");
      if (!rel || rel === "dev/null") continue;
      if (typeof GERRIT_MAGIC_FILES !== "undefined" && (GERRIT_MAGIC_FILES.has(rel) || GERRIT_MAGIC_FILES.has("/" + rel))) continue;
      if (!seenFiles.has(raw)) {
        seenFiles.add(raw);
        filePaths.push(raw);
      }
    }
    const filePathsText = filePaths.join("\n");

    const cherryPickOfChange = c.cherry_pick_of_change ? Number(c.cherry_pick_of_change) : null;
    const cherryPickOfPatchSet = c.cherry_pick_of_patch_set ? Number(c.cherry_pick_of_patch_set) : 1;
    const cherryPickOfRef = cherryPickOfChange ? `${cherryPickOfChange},${cherryPickOfPatchSet}` : "";
    const revertOfChange = c.revert_of ? Number(c.revert_of) : null;

    const curPs = Number(
      (bundle && bundle.patch_set) ||
      (typeof state !== "undefined" && state && state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) ||
      c.patch_set ||
      1
    );

    let repoDownloadCmd = "";
    let gitCheckoutCmd = "";
    let gitCherryPickCmd = "";
    let gitFormatPatchCmd = "";
    let gitPullCmd = "";
    try {
      if (typeof computePgDownloadCommands === "function") {
        const activeScheme = (typeof state !== "undefined" && state && state.downloadScheme) || "repo";
        const cmds = computePgDownloadCommands(c, activeScheme, curPs);
        if (cmds) {
          repoDownloadCmd = cmds.repo || "";
          gitCheckoutCmd = cmds.checkout || "";
          gitCherryPickCmd = cmds["cherry-pick"] || "";
          gitFormatPatchCmd = cmds["format-patch"] || "";
          gitPullCmd = cmds.pull || "";
        }
      }
    } catch (_err) {}

    const refRes = {
      changeId,
      changeIdShort,
      commitSha,
      commitShaShort,
      parentSha,
      parentShaShort,
      parentGitilesUrl,
      parentClNumber,
      parentClSubject,
      parents,
      gerritUrl,
      shortlink,
      titleAndUrl,
      markdown,
      gitilesUrl,
      codeSearchUrl,
      filePaths,
      filePathsText,
      cherryPickOfChange,
      cherryPickOfPatchSet,
      cherryPickOfRef,
      revertOfChange,
      patchSet: curPs,
      repoDownloadCmd,
      gitCheckoutCmd,
      gitCherryPickCmd,
      gitFormatPatchCmd,
      gitPullCmd,
    };
    c._cachedReferenceInfoKey = refCacheKey;
    c._cachedReferenceInfo = refRes;
    const wSub = c._refSub || (c._refSub = {});
    wSub.num = num;
    wSub.subject = c.subject;
    wSub.url = c.url;
    wSub.changeId = c.change_id;
    wSub.commitMsgLen = commitMsgLen;
    wSub.curRev = c.current_revision;
    wSub.project = c.project;
    wSub.parentRev = parentRev;
    wSub.parentsLen = parentsLen;
    wSub.cFilesLen = cFilesLen;
    wSub.cpChange = c.cherry_pick_of_change;
    wSub.cpPs = c.cherry_pick_of_patch_set;
    wSub.revertOf = c.revert_of;
    wSub.ps = c.patch_set;
    wSub.bPs = bPs;
    wSub.bSha = bSha;
    wSub.bParent = bParent;
    wSub.bFilesLen = bFilesLen;
    wSub.dlScheme = dlScheme;
    wSub.selPsStr = selPsStr;
    wSub.changesLen = changesLen;
    return refRes;
  }

  function renderPgCopyLinksMenuHtml(c, bundle = null) {
    if (!c || !c.number) return "";
    const clNum = Number(c.number);
    const info = getPgChangeReferenceInfo(c, bundle);
    const isMenuOpen = Boolean(state.copyLinksMenu && state.copyLinksMenu.isOpen && Number(state.copyLinksMenu.cl) === clNum);
    return AemuGerritReact.renderPgCopyLinksMenuHtml(c, bundle, info, isMenuOpen);
  }

  function renderPgCopyLinksMenuDom() {
    if (typeof document === "undefined") return;
    const menuMeta = state.copyLinksMenu || {};
    const openCl = menuMeta.isOpen ? Number(menuMeta.cl) : null;
    document.querySelectorAll(".pg-copy-links-dropdown").forEach(dropdown => {
      const parentWrap = dropdown.closest(".pg-copy-links-wrap");
      const wrapCl = parentWrap ? Number(parentWrap.id.replace("pgCopyLinksWrap-", "")) : null;
      const shouldBeOpen = Boolean(openCl && wrapCl === openCl);
      dropdown.classList.toggle("is-open", shouldBeOpen);
      if (parentWrap) {
        const btn = parentWrap.querySelector(".pg-btn-copy-links");
        if (btn) {
          btn.classList.toggle("is-active", shouldBeOpen);
          btn.setAttribute("aria-expanded", shouldBeOpen ? "true" : "false");
        }
      }
    });
  }

  function openPgCopyLinksMenu(clNum) {
    state.copyLinksMenu = { isOpen: true, cl: Number(clNum) };
    renderPgCopyLinksMenuDom();
  }

  function closePgCopyLinksMenu() {
    if (state.copyLinksMenu && state.copyLinksMenu.isOpen) {
      state.copyLinksMenu = { isOpen: false, cl: null };
      renderPgCopyLinksMenuDom();
    }
  }

  function togglePgCopyLinksMenu(clNum) {
    const num = Number(clNum);
    if (state.copyLinksMenu && state.copyLinksMenu.isOpen && Number(state.copyLinksMenu.cl) === num) {
      closePgCopyLinksMenu();
    } else {
      openPgCopyLinksMenu(num);
    }
  }

  function copyPgMetaField(clNum, field, btnEl = null) {
    const c = findChangeByNumber(clNum);
    if (!c) return;
    const bundle = (state.diffsByCl && state.diffsByCl[Number(clNum)]) ? state.diffsByCl[Number(clNum)] : null;
    const info = getPgChangeReferenceInfo(c, bundle);
    let text = "";
    let label = "";
    if (field === "change_id") {
      text = info.changeId;
      label = "Change-Id";
    } else if (field === "commit") {
      text = info.commitSha;
      label = "Commit SHA";
    } else if (field === "repo_download" || field === "repo") {
      text = info.repoDownloadCmd;
      label = `Repo download command (PS ${info.patchSet || 1})`;
    } else if (field === "git_checkout" || field === "checkout") {
      text = info.gitCheckoutCmd;
      label = `Git checkout command (PS ${info.patchSet || 1})`;
    } else if (field === "cherry_pick_cmd" || field === "cherry_pick") {
      text = info.gitCherryPickCmd;
      label = `Git cherry-pick command (PS ${info.patchSet || 1})`;
    } else if (field === "format_patch_cmd" || field === "format_patch") {
      text = info.gitFormatPatchCmd;
      label = `Git format-patch command (PS ${info.patchSet || 1})`;
    } else if (field === "cherry_pick_of" || field === "cherrypick") {
      const cpChange = c.cherry_pick_of_change ? Number(c.cherry_pick_of_change) : null;
      const cpPs = c.cherry_pick_of_patch_set ? Number(c.cherry_pick_of_patch_set) : 1;
      text = cpChange ? `${cpChange},${cpPs}` : "";
      label = "Cherry pick reference";
    } else if (field === "revert_of" || field === "revert") {
      const revOf = c.revert_of ? Number(c.revert_of) : null;
      text = revOf ? String(revOf) : "";
      label = "Revert of reference";
    } else if (field === "files" || field === "file_paths") {
      text = info.filePathsText;
      label = `Files (${(info.filePaths || []).length})`;
    } else if (field === "parent" || field === "parent_commit" || field === "parent_sha") {
      text = info.parentSha;
      label = "Parent SHA";
    } else if (field === "url") {
      text = info.gerritUrl;
      label = "Gerrit URL";
    } else if (field === "number") {
      text = String(c.number);
      label = "Change Number";
    } else if (field === "shortlink") {
      text = info.shortlink;
      label = "ag/ shortlink";
    } else if (field === "title_url") {
      text = info.titleAndUrl;
      label = "Title & link";
    } else if (field === "markdown") {
      text = info.markdown;
      label = "Markdown link";
    }
    if (!text) return;
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(text);
    }
    showToast(`Copied ${label}!`);
    if (btnEl && btnEl.classList) {
      const origText = btnEl.textContent;
      btnEl.classList.add("is-copied");
      btnEl.textContent = "✓ Copied";
      setTimeout(() => {
        btnEl.classList.remove("is-copied");
        btnEl.textContent = origText;
      }, 1500);
    }
  }

  function copyPgChangeTitle(clNum) {
    copyPgMetaField(clNum, "title_url");
  }

  function computePgSubmitRequirementsSummary(c) {
    if (!c) {
      return { total: 0, satisfied: 0, status: "pending", label: "✕ Pending", tip: "Submit requirements pending", cls: "sr-chip-pending" };
    }
    const num = Number(c.number || 0);
    const bundle = (num && typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[num]) || null;
    const hasConflict = (typeof isClConflict === "function") && isClConflict(c, bundle);
    const rawList = Array.isArray(c.submit_requirements) && c.submit_requirements.length > 0
      ? c.submit_requirements
      : null;
    const revs = Array.isArray(c.reviewers_detail) ? c.reviewers_detail : null;
    const revsLen = revs ? revs.length : 0;
    const r0Vote = (revs && revs[0]) ? (revs[0].cr_vote || 0) : "";
    const humanCr2Len = Array.isArray(c.human_cr2) ? c.human_cr2.length : 0;
    const sr0Status = (rawList && rawList[0]) ? (rawList[0].status || "") : "";
    const sr1Status = (rawList && rawList.length > 1 && rawList[1]) ? (rawList[1].status || "") : "";
    const srSub = c._srSub;
    if (
      srSub &&
      c._cachedSrSummaryVal &&
      srSub.num === num &&
      srSub.conflict === hasConflict &&
      srSub.isSub === c.is_submittable &&
      srSub.rawLen === (rawList ? rawList.length : 0) &&
      srSub.sr0 === sr0Status &&
      srSub.sr1 === sr1Status &&
      srSub.reqCr2 === c.required_cr2_count &&
      srSub.agent === c.is_agent_cl &&
      srSub.hCr2Len === humanCr2Len &&
      srSub.hCr2Cnt === c.human_cr2_count &&
      srSub.pvSt === c.presubmit_status &&
      srSub.pvSc === c.presubmit_score &&
      srSub.pvVet === c.presubmit_vetted &&
      srSub.vCr === c.viewer_cr_vote &&
      srSub.appByV === c.approved_by_viewer &&
      srSub.revsLen === revsLen &&
      srSub.r0Vote === r0Vote
    ) {
      return c._cachedSrSummaryVal;
    }
    const srSig = rawList
      ? `${num}|${hasConflict ? 1 : 0}|${c.is_submittable ? 1 : 0}|sr:${rawList.length}:${sr0Status}:${sr1Status}`
      : `${num}|${hasConflict ? 1 : 0}|${c.is_submittable ? 1 : 0}|${c.required_cr2_count || 0}|${c.is_agent_cl ? 1 : 0}|${humanCr2Len}|${c.human_cr2_count || 0}|${c.presubmit_status || ""}|${c.presubmit_score || 0}|${c.presubmit_vetted ? 1 : 0}|${c.viewer_cr_vote || 0}|${c.approved_by_viewer ? 1 : 0}|${revsLen}:${r0Vote}`;
    const saveSrSub = (val) => {
      c._cachedSrSummarySig = srSig;
      c._cachedSrSummaryVal = val;
      const wSub = c._srSub || (c._srSub = {});
      wSub.num = num;
      wSub.conflict = hasConflict;
      wSub.isSub = c.is_submittable;
      wSub.rawLen = rawList ? rawList.length : 0;
      wSub.sr0 = sr0Status;
      wSub.sr1 = sr1Status;
      wSub.reqCr2 = c.required_cr2_count;
      wSub.agent = c.is_agent_cl;
      wSub.hCr2Len = humanCr2Len;
      wSub.hCr2Cnt = c.human_cr2_count;
      wSub.pvSt = c.presubmit_status;
      wSub.pvSc = c.presubmit_score;
      wSub.pvVet = c.presubmit_vetted;
      wSub.vCr = c.viewer_cr_vote;
      wSub.appByV = c.approved_by_viewer;
      wSub.revsLen = revsLen;
      wSub.r0Vote = r0Vote;
      return val;
    };
    if (c._cachedSrSummarySig === srSig && c._cachedSrSummaryVal) {
      return saveSrSub(c._cachedSrSummaryVal);
    }
    let res;
    if (hasConflict) {
      let total = 0;
      let satisfied = 0;
      if (rawList) {
        for (let i = 0; i < rawList.length; i++) {
          const s = (rawList[i].status || "").toUpperCase();
          if (s !== "NOT_APPLICABLE") {
            total++;
            if (s === "SATISFIED" || s === "OVERRIDDEN") satisfied++;
          }
        }
      }
      total = Math.max(total, 1);
      res = {
        total,
        satisfied,
        status: "conflict",
        label: "✕ Merge Conflict",
        tip: "Change has merge conflicts with target branch. Rebase required. Click to view requirements.",
        cls: "sr-chip-blocked sr-chip-conflict",
      };
    } else if (rawList) {
      let total = 0;
      let satisfied = 0;
      let isBlocked = false;
      for (let i = 0; i < rawList.length; i++) {
        const s = (rawList[i].status || "").toUpperCase();
        if (s !== "NOT_APPLICABLE") {
          total++;
          if (s === "SATISFIED" || s === "OVERRIDDEN") satisfied++;
          else if (s === "BLOCKED") isBlocked = true;
        }
      }
      const isSubmittable = Boolean(c.is_submittable || (total > 0 && satisfied === total && !isBlocked));
      if (isSubmittable) {
        res = {
          total,
          satisfied,
          status: "submittable",
          label: "✓ Submittable",
          tip: "All submit requirements satisfied. Click to view requirements.",
          cls: "sr-chip-submittable",
        };
      } else if (isBlocked) {
        res = {
          total,
          satisfied,
          status: "blocked",
          label: "✕ Blocked",
          tip: "Submit requirement blocked. Click to view requirements.",
          cls: "sr-chip-blocked",
        };
      } else {
        res = {
          total,
          satisfied,
          status: "pending",
          label: `✕ ${satisfied}/${total} satisfied`,
          tip: `${total - satisfied} submit requirement(s) pending approval. Click to view requirements.`,
          cls: "sr-chip-pending",
        };
      }
    } else {
      // Fallback heuristic based on CR and PV
      const voterEntries = collectCrVoterEntriesForSr(c);
      let minVote = 0;
      for (let i = 0; i < voterEntries.length; i++) {
        if (voterEntries[i].val < minVote) minVote = voterEntries[i].val;
      }
      const reqCr2 = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
      const doneCr2 = Math.max(humanCr2Len, Number(c.human_cr2_count || 0));
      const crSatisfied = minVote > -2 && doneCr2 >= reqCr2 && reqCr2 > 0;
      const pv = getClPresubmitDisplay(c);
      const pvScore = Number(c.presubmit_score || 0);
      const pvSatisfied = Boolean(c.presubmit_vetted || pvScore >= 2 || pv.status === "passed");
      const isBlocked = minVote <= -2 || pvScore < 0 || pv.status === "failed";
      const total = 2;
      const satisfied = (crSatisfied ? 1 : 0) + (pvSatisfied ? 1 : 0);
      if (c.is_submittable || (crSatisfied && pvSatisfied && !isBlocked)) {
        res = {
          total,
          satisfied: total,
          status: "submittable",
          label: "✓ Submittable",
          tip: "All submit requirements satisfied. Click to view requirements.",
          cls: "sr-chip-submittable",
        };
      } else if (isBlocked) {
        res = {
          total,
          satisfied,
          status: "blocked",
          label: "✕ Blocked",
          tip: minVote <= -2 ? "Blocked by Code-Review -2. Click to view requirements." : "Blocked by presubmit failure. Click to view requirements.",
          cls: "sr-chip-blocked",
        };
      } else {
        res = {
          total,
          satisfied,
          status: "pending",
          label: `✕ ${satisfied}/${total} satisfied`,
          tip: `${total - satisfied} submit requirement(s) pending approval. Click to view requirements.`,
          cls: "sr-chip-pending",
        };
      }
    }
    return saveSrSub(res);
  }

  function renderPgSrSummaryChipHtml(c) {
    const srSummary = computePgSubmitRequirementsSummary(c);
    return AemuGerritReact.renderPgSrSummaryChipHtml(Number(c && c.number), srSummary);
  }

  function scrollToSubmitRequirements(clNum) {
    const num = Number(clNum);
    const slot = document.getElementById(`pgSrSlot-${num}`);
    if (slot) {
      if (typeof slot.scrollIntoView === "function") {
        slot.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      slot.classList.add("pg-chunk-focused-flash");
      setTimeout(() => slot.classList.remove("pg-chunk-focused-flash"), 1800);
    }
  }

  function renderPgChangeHeaderHtml(c, bundle) {
    const clNum = Number(c.number);
    const st = computePgHeaderStatusInfo(c, bundle);
    const srSummary = computePgSubmitRequirementsSummary(c);
    const usePassRef = _curCommitRenderPass.active && _curCommitRenderPass.clNum === clNum && _curCommitRenderPass.refBundle === bundle && _curCommitRenderPass.refInfo;
    const refInfo = usePassRef ? _curCommitRenderPass.refInfo : getPgChangeReferenceInfo(c, bundle);
    if (_curCommitRenderPass.active && _curCommitRenderPass.clNum === clNum) {
      _curCommitRenderPass.refBundle = bundle;
      _curCommitRenderPass.refInfo = refInfo;
    }
    const isCopyMenuOpen = Boolean(state.copyLinksMenu && state.copyLinksMenu.isOpen && Number(state.copyLinksMenu.cl) === clNum);
    const isStarred = isClStarred(c);
    const isViewerTurn = isClInViewerAttentionSet(c);
    const isStacked = typeof window !== "undefined" && (window.innerWidth || 1200) <= 720;
    const expandSidebarVisible = Boolean(state.sidebarCollapsed || isStacked);
    const visCommitCount = Number(state.visibleCommitCount || 0);
    const drafts = c._renderPassDrafts || (typeof getPendingDraftsForCl === "function" ? getPendingDraftsForCl(c.number) : []);
    const draftCount = Array.isArray(drafts) ? drafts.length : 0;
    const submitTarget = c._renderPassSubmitTarget || computeCommitSubmitTarget(c);
    const isBusy = Boolean(state.submittingInFlight || state.queueLoading);
    const hasActiveEdit = Boolean(state.activeEditsByCl && state.activeEditsByCl[clNum]);
    const bForMap = bundle || (typeof state !== "undefined" && state && state.diffsByCl && state.diffsByCl[clNum]) || null;
    const usePassMap = _curCommitRenderPass.active && _curCommitRenderPass.clNum === clNum && _curCommitRenderPass.mapBundle === bForMap && _curCommitRenderPass.mapUrl;
    const aemuMapUrl = usePassMap ? _curCommitRenderPass.mapUrl : buildAemuMapCommitUrl(c, bForMap);
    if (_curCommitRenderPass.active && _curCommitRenderPass.clNum === clNum) {
      _curCommitRenderPass.mapBundle = bForMap;
      _curCommitRenderPass.mapUrl = aemuMapUrl;
    }
    const canNav = (state._renderPassCanNav !== undefined) ? state._renderPassCanNav : canNavigateVisibleCls();
    const cvActive = Boolean(state.codeViewerMode);
    const cvHeaderFileCount = (bForMap && Array.isArray(bForMap.files))
      ? bForMap.files.length
      : (Array.isArray(c.files) ? c.files.length : 0);
    const flagsMask =
      (isCopyMenuOpen ? 1 : 0) |
      (isStarred ? 2 : 0) |
      (isViewerTurn ? 4 : 0) |
      (expandSidebarVisible ? 8 : 0) |
      (isBusy ? 16 : 0) |
      (hasActiveEdit ? 32 : 0) |
      (c.work_in_progress ? 64 : 0) |
      (canNav ? 128 : 0) |
      (cvActive ? 256 : 0);
    const hashtagsLen = Array.isArray(c.hashtags) ? c.hashtags.length : 0;
    const firstHashtag = hashtagsLen > 0 ? c.hashtags[0] : "";
    const dlScheme = state.downloadScheme || "repo";
    const hdrSub = c._hdrSub;
    if (
      hdrSub &&
      typeof c._cachedFullChangeHeaderHtml === "string" &&
      hdrSub.clNum === clNum &&
      hdrSub.st === st &&
      hdrSub.srSummary === srSummary &&
      hdrSub.refInfo === refInfo &&
      hdrSub.submitTarget === submitTarget &&
      hdrSub.aemuMapUrl === aemuMapUrl &&
      hdrSub.draftCount === draftCount &&
      hdrSub.visCommitCount === visCommitCount &&
      hdrSub.cvFileCount === cvHeaderFileCount &&
      hdrSub.flagsMask === flagsMask &&
      hdrSub.subject === c.subject &&
      hdrSub.topic === c.topic &&
      hdrSub.branch === c.branch &&
      hdrSub.project === c.project &&
      hdrSub.author === c.author &&
      hdrSub.ownerName === c.owner_name &&
      hdrSub.updated === c.updated &&
      hdrSub.status === c.status &&
      hdrSub.hashtagsLen === hashtagsLen &&
      hdrSub.firstHashtag === firstHashtag &&
      hdrSub.dlScheme === dlScheme
    ) {
      return c._cachedFullChangeHeaderHtml;
    }
    const hashtagsSig = hashtagsLen > 0 ? c.hashtags.join(",") : "";
    const titleBarCacheKey = `${clNum}|${c.subject || ""}|${c.topic || ""}|${c.branch || ""}|${c.project || ""}|${c.author || ""}|${c.owner_name || ""}|${c.updated || ""}|${st.cls}|${st.label}|${st.tip}|${st.secondaryCls || ""}|${srSummary.total}:${srSummary.satisfied}:${srSummary.unsatisfied}:${srSummary.isReady ? 1 : 0}|${c._cachedReferenceInfoKey || refInfo.commitSha || ""}|${isCopyMenuOpen ? 1 : 0}|${isStarred ? 1 : 0}|${isViewerTurn ? 1 : 0}|${expandSidebarVisible ? 1 : 0}|${visCommitCount}|${hashtagsSig}|${dlScheme}`;
    const stickyCacheKey = `${clNum}|${draftCount}`;
    const fullHeaderCacheKey = `${titleBarCacheKey}|${stickyCacheKey}|${submitTarget.visible ? 1 : 0}:${submitTarget.disabled ? 1 : 0}:${submitTarget.label || ""}:${submitTarget.title || ""}|${isBusy ? 1 : 0}|${hasActiveEdit ? 1 : 0}|${c.status || ""}|${c.work_in_progress ? 1 : 0}|${aemuMapUrl}|${canNav ? 1 : 0}|${cvActive ? 1 : 0}:${cvHeaderFileCount}`;
    if (c._cachedFullChangeHeaderKey === fullHeaderCacheKey && typeof c._cachedFullChangeHeaderHtml === "string") {
      return c._cachedFullChangeHeaderHtml;
    }
    /* class="pg-change-number commit-cl-id" */
    let titleBarHtml = (c._cachedTitleBarKey === titleBarCacheKey) ? c._cachedTitleBarHtml : undefined;
    if (titleBarHtml === undefined) {
      titleBarHtml = AemuGerritReact.renderPgChangeHeaderTitleBarHtml({
        change: c,
        bundle,
        statusInfo: st,
        srSummary,
        refInfo,
        isCopyMenuOpen,
        isStarred,
        isViewerTurn,
        expandSidebarVisible,
        visibleCommitCount: visCommitCount,
      });
      c._cachedTitleBarKey = titleBarCacheKey;
      c._cachedTitleBarHtml = titleBarHtml;
    }
    let stickyScrollActionsHtml = (c._cachedStickyActionsKey === stickyCacheKey) ? c._cachedStickyActionsHtml : undefined;
    if (stickyScrollActionsHtml === undefined) {
      stickyScrollActionsHtml = AemuGerritReact.renderPgStickyScrollActionsHtml(c.number, draftCount);
      c._cachedStickyActionsKey = stickyCacheKey;
      c._cachedStickyActionsHtml = stickyScrollActionsHtml;
    }
    c._loopSubmitTarget = submitTarget;
    c._loopAemuMapBundle = bForMap;
    c._loopAemuMapUrl = aemuMapUrl;
    const fullHeaderHtml = `<header id="pgChangeHeader-${c.number}" class="pg-change-header commit-sec-header ${st.cls}">
      ${titleBarHtml}
      <div class="pg-commit-actions commit-sec-actions">
        ${stickyScrollActionsHtml}
        ${renderCommitSubmitButtonHtml(c)}
        ${renderPgHeaderActionButtonsHtml(c)}
        ${renderCommitHarnessActionsHtml(c, bundle)}
      </div>
    </header>`;
    c._loopSubmitTarget = null;
    c._loopAemuMapBundle = null;
    c._loopAemuMapUrl = "";
    c._cachedFullChangeHeaderKey = fullHeaderCacheKey;
    c._cachedFullChangeHeaderHtml = fullHeaderHtml;
    const wHdr = c._hdrSub || (c._hdrSub = {});
    wHdr.clNum = clNum;
    wHdr.st = st;
    wHdr.srSummary = srSummary;
    wHdr.refInfo = refInfo;
    wHdr.submitTarget = submitTarget;
    wHdr.aemuMapUrl = aemuMapUrl;
    wHdr.draftCount = draftCount;
    wHdr.visCommitCount = visCommitCount;
    wHdr.cvFileCount = cvHeaderFileCount;
    wHdr.flagsMask = flagsMask;
    wHdr.subject = c.subject;
    wHdr.topic = c.topic;
    wHdr.branch = c.branch;
    wHdr.project = c.project;
    wHdr.author = c.author;
    wHdr.ownerName = c.owner_name;
    wHdr.updated = c.updated;
    wHdr.status = c.status;
    wHdr.hashtagsLen = hashtagsLen;
    wHdr.firstHashtag = firstHashtag;
    wHdr.dlScheme = dlScheme;
    return fullHeaderHtml;
  }

  let _pgReactBridgeSingleton = null;

  function getPgReactBridge() {
    if (!_pgReactBridgeSingleton) {
      _pgReactBridgeSingleton = AemuGerritReact.createHostBridge({
        getFallbackReviewersForCl: (peer) => getFallbackReviewersForCl(peer),
        isClConflict: (change, bundle) => isClConflict(change, bundle),
        isClSubmittable: (change) => isClSubmittable(change),
        getChangeRebaseStatus: (change, bundle) => getPgChangeRebaseStatus(change, bundle),
        getBranchDisplay: (branch) => getPgBranchDisplay(branch),
        getProjectDisplay: (project) => getPgProjectDisplay(project),
        getChangeId: (change) => getPgChangeId(change),
        findStackForClOrId: (clNum, preferredStackId) => findPgStackForClOrId(clNum, preferredStackId),
        getTopicPeers: (change) => getPgTopicPeers(change),
        getCherryPickPeers: (change) => getPgCherryPickPeers(change),
        getCherryPickGroup: (change) => getPgCherryPickGroup(change),
        getSubmittedTogetherPeers: (change) => getPgSubmittedTogetherPeers(change),
        getRelatedChangesTab: (clNum) => getPgRelatedChangesTab(clNum),
        canStackInterdiff: (commits) => canPgStackInterdiff(commits),
        isStackInterdiffActive: (commits, stackId) => isPgStackInterdiffActive(commits, stackId),
        getPersistedStackInterdiff: (stackId) => getPgPersistedStackInterdiff(stackId),
        ensureStackInterdiffRestored: (st) => ensurePgStackInterdiffRestored(st),
        getSelectedPatchset: (clNum) => (state.selectedPatchsetByCl && state.selectedPatchsetByCl[clNum]) || getPgSelectedPatchset(clNum) || {},
        getDiffBundle: (clNum) => (state.diffsByCl && state.diffsByCl[clNum]) || null,
        isContinuousStackDiff: () => Boolean(state.continuousStackDiff),
        getSelectedStackId: () => state.selectedStackId,
        getSelectedClNumber: () => state.selectedClNumber,
        isBatchModeActive: () => Boolean(state.checkedClNumbers && state.checkedClNumbers.size > 0),
        isClChecked: (clNum) => Boolean(state.checkedClNumbers && state.checkedClNumbers.has(clNum)),
        isStackCommitReviewed: (clNum) => isPgStackCommitReviewed(clNum),
        isStackCommitCollapsed: (clNum) => Boolean(state.collapsedStackCommits && state.collapsedStackCommits.has(Number(clNum))),
        getStackSubmitSequence: (activeStack, commits) => getPgStackSubmitSequence(activeStack, commits),
      });
    }
    return _pgReactBridgeSingleton;
  }

  function renderPgRebaseNoticeBannerHtml(c, bundle = null) {
    if (!c) return "";
    const reb = getPgChangeRebaseStatus(c, bundle);
    if (!reb || (!reb.needsRebase && !reb.hasConflict && !reb.isOutdated && !reb.isParentMerged)) {
      return "";
    }
    const rbSub = c._rebBannerSub;
    if (
      rbSub &&
      rbSub.rebRef === reb &&
      rbSub.num === (c.number || 0) &&
      c._cachedRebaseBannerKey === rbSub.key &&
      typeof c._cachedRebaseBannerHtml === "string"
    ) {
      return c._cachedRebaseBannerHtml;
    }
    const conflictsSig = Array.isArray(reb.conflicts) ? reb.conflicts.join(",") : "";
    const rebCacheKey = `${c.number || 0}|${reb.hasConflict ? 1 : 0}|${reb.isOutdated ? 1 : 0}|${reb.isIndirectlyOutdated ? 1 : 0}|${reb.isParentMerged ? 1 : 0}|${reb.parentClNumber || 0}|${reb.parentCurrentPs || 0}|${reb.reason || ""}|${conflictsSig}`;
    if (c._cachedRebaseBannerKey === rebCacheKey && typeof c._cachedRebaseBannerHtml === "string") {
      c._rebBannerSub = { rebRef: reb, num: c.number || 0, key: rebCacheKey };
      return c._cachedRebaseBannerHtml;
    }
    const html = AemuGerritReact.renderPgRebaseNoticeBannerHtml(c, bundle, getPgReactBridge());
    c._cachedRebaseBannerKey = rebCacheKey;
    c._cachedRebaseBannerHtml = html;
    c._rebBannerSub = { rebRef: reb, num: c.number || 0, key: rebCacheKey };
    return html;
  }

  function getPgBranchDisplay(branch) {
    if (!branch) return "";
    let b = String(branch).trim();
    if (b.startsWith("refs/heads/")) {
      b = b.slice("refs/heads/".length);
    }
    return b;
  }

  function getPgProjectDisplay(project) {
    if (!project) return "";
    let p = String(project).trim();
    const parts = p.split("/");
    return parts[parts.length - 1] || p;
  }

  let _pgPeersIdxChangesRef = null;
  let _pgPeersIdxChangesLen = -1;
  let _pgPeersIdxStacksRef = null;
  let _pgPeersIdxStacksLen = -1;
  let _pgPeersIdxFirstNum = -1;
  let _pgPeersIdxLastNum = -1;
  let _pgPeersIdxFirstStId = null;
  let _pgPeersIdxLastStId = null;
  let _pgPeersByTopic = null;
  let _pgPeersByCid = null;

  function ensurePgPeersLookupIndex() {
    const changes = Array.isArray(state.changes) ? state.changes : _PG_EMPTY_ARRAY;
    const stacks = Array.isArray(state.stacks) ? state.stacks : _PG_EMPTY_ARRAY;
    const changesLen = changes.length;
    const stacksLen = stacks.length;
    const firstNum = changesLen > 0 && changes[0] ? (changes[0].number || 0) : 0;
    const lastNum = changesLen > 0 && changes[changesLen - 1] ? (changes[changesLen - 1].number || 0) : 0;
    const firstStId = stacksLen > 0 && stacks[0] ? (stacks[0].stack_id || "") : "";
    const lastStId = stacksLen > 0 && stacks[stacksLen - 1] ? (stacks[stacksLen - 1].stack_id || "") : "";
    if (
      _pgPeersByTopic &&
      _pgPeersByCid &&
      _pgPeersIdxChangesRef === changes &&
      _pgPeersIdxChangesLen === changesLen &&
      _pgPeersIdxStacksRef === stacks &&
      _pgPeersIdxStacksLen === stacksLen &&
      _pgPeersIdxFirstNum === firstNum &&
      _pgPeersIdxLastNum === lastNum &&
      _pgPeersIdxFirstStId === firstStId &&
      _pgPeersIdxLastStId === lastStId
    ) {
      return;
    }
    const byTopic = new Map();
    const byCid = new Map();
    const indexCommit = (x) => {
      if (!x) return;
      const xNum = Number(x.number);
      if (!xNum) return;
      if (x.topic) {
        const t = String(x.topic).trim().toLowerCase();
        if (t) {
          let tMap = byTopic.get(t);
          if (!tMap) {
            tMap = new Map();
            byTopic.set(t, tMap);
          }
          if (!tMap.has(xNum)) {
            tMap.set(xNum, x);
          }
        }
      }
      const cid = getPgChangeId(x).toLowerCase();
      if (cid) {
        let cMap = byCid.get(cid);
        if (!cMap) {
          cMap = new Map();
          byCid.set(cid, cMap);
        }
        if (!cMap.has(xNum)) {
          cMap.set(xNum, x);
        }
      }
    };
    for (let i = 0; i < changesLen; i++) {
      indexCommit(changes[i]);
    }
    for (let i = 0; i < stacksLen; i++) {
      const st = stacks[i];
      if (st && Array.isArray(st.commits)) {
        const commits = st.commits;
        for (let j = 0; j < commits.length; j++) {
          indexCommit(commits[j]);
        }
      }
    }
    const sortedByTopic = new Map();
    byTopic.forEach((tMap, t) => {
      sortedByTopic.set(t, Array.from(tMap.values()).sort((a, b) => Number(a.number) - Number(b.number)));
    });
    const sortedByCid = new Map();
    byCid.forEach((cMap, cid) => {
      sortedByCid.set(cid, Array.from(cMap.values()).sort((a, b) => Number(a.number) - Number(b.number)));
    });
    _pgPeersIdxChangesRef = changes;
    _pgPeersIdxChangesLen = changesLen;
    _pgPeersIdxStacksRef = stacks;
    _pgPeersIdxStacksLen = stacksLen;
    _pgPeersIdxFirstNum = firstNum;
    _pgPeersIdxLastNum = lastNum;
    _pgPeersIdxFirstStId = firstStId;
    _pgPeersIdxLastStId = lastStId;
    _pgPeersByTopic = sortedByTopic;
    _pgPeersByCid = sortedByCid;
  }

  function getPgTopicPeers(c) {
    if (!c || !c.topic) return _PG_EMPTY_ARRAY;
    const topicName = String(c.topic || "").trim().toLowerCase();
    if (!topicName) return _PG_EMPTY_ARRAY;
    const changesLen = Array.isArray(state.changes) ? state.changes.length : 0;
    const stacksLen = Array.isArray(state.stacks) ? state.stacks.length : 0;
    const firstNum = (state.changes && state.changes[0]) ? state.changes[0].number : 0;
    if (
      c._topicPeersSubTopic === topicName &&
      c._topicPeersSubChLen === changesLen &&
      c._topicPeersSubStLen === stacksLen &&
      c._topicPeersSubFirst === firstNum &&
      Array.isArray(c._cachedTopicPeers)
    ) {
      return c._cachedTopicPeers;
    }
    const sig = `${topicName}:${changesLen}:${stacksLen}:${firstNum}`;
    if (c._cachedTopicPeersSig === sig && Array.isArray(c._cachedTopicPeers)) {
      c._topicPeersSubTopic = topicName;
      c._topicPeersSubChLen = changesLen;
      c._topicPeersSubStLen = stacksLen;
      c._topicPeersSubFirst = firstNum;
      return c._cachedTopicPeers;
    }
    ensurePgPeersLookupIndex();
    let res = _pgPeersByTopic.get(topicName) || _PG_EMPTY_ARRAY;
    if (c.number && res.length === 0 && Array.isArray(state.changes) && state.changes.includes(c)) {
      _pgPeersByTopic = null;
      ensurePgPeersLookupIndex();
      res = _pgPeersByTopic.get(topicName) || _PG_EMPTY_ARRAY;
    }
    c._cachedTopicPeersSig = sig;
    c._cachedTopicPeers = res;
    c._topicPeersSubTopic = topicName;
    c._topicPeersSubChLen = changesLen;
    c._topicPeersSubStLen = stacksLen;
    c._topicPeersSubFirst = firstNum;
    return res;
  }

  const _PG_CHANGE_ID_RE = /Change-Id:\s*(I[0-9a-fA-F]{40})/;

  function getPgChangeId(c) {
    if (!c) return "";
    if (c.change_id && typeof c.change_id === "string") {
      return c.change_id.trim();
    }
    const msg = String(c.commit_message || c.message || "");
    if (c._cachedChangeIdMsg === msg && typeof c._cachedChangeId === "string") {
      return c._cachedChangeId;
    }
    const m = msg.match(_PG_CHANGE_ID_RE);
    const val = m ? m[1] : "";
    c._cachedChangeIdMsg = msg;
    c._cachedChangeId = val;
    return val;
  }

  function getPgCherryPickPeers(c) {
    if (!c) return _PG_EMPTY_ARRAY;
    const curNum = Number(c.number);
    const cid = getPgChangeId(c).toLowerCase();
    if (!cid) return _PG_EMPTY_ARRAY;
    const changesLen = Array.isArray(state.changes) ? state.changes.length : 0;
    const stacksLen = Array.isArray(state.stacks) ? state.stacks.length : 0;
    if (
      c._cpPeersSubNum === curNum &&
      c._cpPeersSubCid === cid &&
      c._cpPeersSubChLen === changesLen &&
      c._cpPeersSubStLen === stacksLen &&
      Array.isArray(c._cachedCherryPickPeers)
    ) {
      return c._cachedCherryPickPeers;
    }
    const sig = `${curNum}:${cid}:${changesLen}:${stacksLen}`;
    if (c._cachedCherryPickPeersSig === sig && Array.isArray(c._cachedCherryPickPeers)) {
      c._cpPeersSubNum = curNum;
      c._cpPeersSubCid = cid;
      c._cpPeersSubChLen = changesLen;
      c._cpPeersSubStLen = stacksLen;
      return c._cachedCherryPickPeers;
    }
    ensurePgPeersLookupIndex();
    const group = _pgPeersByCid.get(cid);
    let res = _PG_EMPTY_ARRAY;
    if (group && group.length > 0) {
      const filtered = group.filter(x => Number(x.number) !== curNum);
      if (filtered.length > 0) res = filtered;
    }
    c._cachedCherryPickPeersSig = sig;
    c._cachedCherryPickPeers = res;
    c._cpPeersSubNum = curNum;
    c._cpPeersSubCid = cid;
    c._cpPeersSubChLen = changesLen;
    c._cpPeersSubStLen = stacksLen;
    return res;
  }

  function getPgCherryPickGroup(c) {
    if (!c) return [];
    const curNum = Number(c.number);
    const cid = getPgChangeId(c).toLowerCase();
    if (!cid) return curNum ? [c] : [];
    ensurePgPeersLookupIndex();
    const group = _pgPeersByCid.get(cid);
    if (group && group.length > 0) {
      if (!curNum || group.some(x => Number(x.number) === curNum)) {
        return group.slice();
      }
      return group.concat([c]).sort((a, b) => Number(a.number) - Number(b.number));
    }
    return curNum ? [c] : [];
  }

  function getPgSubmittedTogetherPeers(c) {
    if (!c) return _PG_EMPTY_ARRAY;
    const curNum = Number(c.number || 0);
    const st = findPgStackForClOrId(curNum, state.selectedStackId);
    const stCommitsSig = (st && Array.isArray(st.commits))
      ? computeStackRebaseRevisionSig(st.commits)
      : "";
    const explicitStLen = Array.isArray(c.submitted_together) ? c.submitted_together.length : 0;
    const changesLen = Array.isArray(state.changes) ? state.changes.length : 0;
    const stSub = c._stPeersSub;
    if (
      stSub &&
      stSub.num === curNum &&
      stSub.status === (c.status || "") &&
      stSub.merged === Boolean(c.is_merged) &&
      stSub.abandoned === Boolean(c.is_abandoned) &&
      stSub.topic === (c.topic || "") &&
      stSub.explicitLen === explicitStLen &&
      stSub.stCommitsSig === stCommitsSig &&
      stSub.changesLen === changesLen &&
      c._cachedSubTogetherSig === stSub.sig &&
      Array.isArray(c._cachedSubTogetherPeers)
    ) {
      return c._cachedSubTogetherPeers;
    }
    const sig = `${curNum}:${c.status || ""}:${c.is_merged ? 1 : 0}:${c.is_abandoned ? 1 : 0}:${c.topic || ""}:${explicitStLen}:${stCommitsSig}:${changesLen}`;
    if (c._cachedSubTogetherSig === sig && Array.isArray(c._cachedSubTogetherPeers)) {
      c._stPeersSub = {
        num: curNum,
        status: c.status || "",
        merged: Boolean(c.is_merged),
        abandoned: Boolean(c.is_abandoned),
        topic: c.topic || "",
        explicitLen: explicitStLen,
        stCommitsSig,
        changesLen,
        sig,
      };
      return c._cachedSubTogetherPeers;
    }

    // 1. If change already has explicit submitted_together data from Gerrit REST API
    if (Array.isArray(c.submitted_together) && c.submitted_together.length > 0) {
      const peers = [];
      const seen = new Set();
      c.submitted_together.forEach(item => {
        const itemNum = Number(typeof item === "object" ? (item.number || item._number) : item);
        if (!itemNum || seen.has(itemNum)) return;
        seen.add(itemNum);
        let match = (state.changes || []).find(x => Number(x.number) === itemNum);
        if (!match && Array.isArray(state.stacks)) {
          for (const s of state.stacks) {
            const found = (s.commits || []).find(x => Number(x.number) === itemNum);
            if (found) { match = found; break; }
          }
        }
        if (!match && typeof item === "object") match = item;
        if (match) peers.push(match);
      });
      if (peers.length > 0) {
        c._cachedSubTogetherSig = sig;
        c._cachedSubTogetherPeers = peers;
        c._stPeersSub = {
          num: curNum,
          status: c.status || "",
          merged: Boolean(c.is_merged),
          abandoned: Boolean(c.is_abandoned),
          topic: c.topic || "",
          explicitLen: explicitStLen,
          stCommitsSig,
          changesLen,
          sig,
        };
        return peers;
      }
    }

    // 2. Compute submitted together peers based on Gerrit submit rules (Topic + Stack dependencies)
    const map = new Map();
    const consider = (x) => {
      if (!x) return;
      const xNum = Number(x.number);
      if (!xNum || map.has(xNum)) return;
      const stUpper = String(x.status || "").toUpperCase();
      if (stUpper === "MERGED" || Boolean(x.is_merged) || stUpper === "ABANDONED" || Boolean(x.is_abandoned)) {
        return;
      }
      map.set(xNum, x);
    };

    // Add current change if not merged/abandoned
    const curStatus = String(c.status || "").toUpperCase();
    if (curStatus !== "MERGED" && !c.is_merged && curStatus !== "ABANDONED" && !c.is_abandoned) {
      map.set(curNum, c);
    }

    // Add open stack commits (in Gerrit, submitting in a relation chain submits all open ancestor/descendant changes in the stack)
    if (st && Array.isArray(st.commits)) {
      st.commits.forEach(commit => consider(commit));
    }

    // Add open topic peers (in Gerrit, submit whole topic submits all open changes sharing the topic)
    const topicPeers = getPgTopicPeers(c);
    topicPeers.forEach(peer => consider(peer));

    const list = Array.from(map.values());
    if (st && Array.isArray(st.commits)) {
      const commitOrderMap = new Map();
      st.commits.forEach((commit, idx) => commitOrderMap.set(Number(commit.number), idx));
      list.sort((a, b) => {
        const orderA = commitOrderMap.has(Number(a.number)) ? commitOrderMap.get(Number(a.number)) : 9999;
        const orderB = commitOrderMap.has(Number(b.number)) ? commitOrderMap.get(Number(b.number)) : 9999;
        if (orderA !== orderB) return orderA - orderB;
        return Number(a.number) - Number(b.number);
      });
    } else {
      list.sort((a, b) => Number(a.number) - Number(b.number));
    }

    c._cachedSubTogetherSig = sig;
    c._cachedSubTogetherPeers = list;
    c._stPeersSub = {
      num: curNum,
      status: c.status || "",
      merged: Boolean(c.is_merged),
      abandoned: Boolean(c.is_abandoned),
      topic: c.topic || "",
      explicitLen: explicitStLen,
      stCommitsSig,
      changesLen,
      sig,
    };
    return list;
  }

  function applyPgSubmittedTogetherFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const c = (state.changes || []).find(x => Number(x.number) === num) || { number: num };
    const peers = getPgSubmittedTogetherPeers(c);
    const nums = peers.map(p => Number(p.number)).filter(Boolean);
    if (!nums.length) return;
    const q = nums.map(n => `change:${n}`).join(" OR ");
    state.searchQuery = q;
    state.authorFilter = "all";
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = q;
    if (typeof syncSearchClearButton === "function") syncSearchClearButton();
    if (typeof renderPgSearchPresetsDom === "function") renderPgSearchPresetsDom();
    if (typeof renderAll === "function") renderAll();
    if (typeof showToast === "function") {
      showToast(`Filtered dashboard to ${nums.length} submitted together changes ✓`, 2000);
    }
  }

  function copyPgChangeId(changeId) {
    const cid = String(changeId || "").trim();
    if (!cid) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(cid).catch(() => {});
      }
    } catch (_) {}
    showToast(`Copied Change-Id "${cid}" to clipboard ✓`, 2000);
  }

  function applyPgChangeIdFilter(changeId) {
    const cid = String(changeId || "").trim();
    if (!cid) return;
    const q = `change:"${cid}"`;
    state.searchQuery = q;
    state.authorFilter = "all";
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = q;
    syncSearchClearButton();
    renderPgSearchPresetsDom();
    renderAll();
    showToast(`Filtered dashboard by Change-Id: "${cid}" ✓`, 2000);
  }

  function getPgRelatedChangesTab(clNum, cOpt = null, stOpt = undefined) {
    const num = Number(clNum || 0);
    const saved = num && state.relatedChangesTabByCl && state.relatedChangesTabByCl[num];
    const st = stOpt !== undefined ? stOpt : findPgStackForClOrId(num, state.selectedStackId);
    const hasStack = Boolean(st && Array.isArray(st.commits) && st.commits.length > 1);
    if (hasStack && (!saved || saved === "chain")) {
      return "chain";
    }
    const c = cOpt || findPgChangeByNumber(num) || { number: num };
    if (!saved && !hasStack) {
      const stId = st ? (st.stack_id || "") : "";
      const subLen = Array.isArray(c.submitted_together) ? c.submitted_together.length : 0;
      const changesLen = Array.isArray(state.changes) ? state.changes.length : 0;
      const stacksLen = Array.isArray(state.stacks) ? state.stacks.length : 0;
      const chId = getPgChangeId(c);
      const rtSub = c._relTabSub;
      if (
        rtSub &&
        typeof rtSub.tab === "string" &&
        rtSub.num === num &&
        rtSub.stId === stId &&
        rtSub.status === c.status &&
        rtSub.merged === c.is_merged &&
        rtSub.aband === c.is_abandoned &&
        rtSub.topic === c.topic &&
        rtSub.subLen === subLen &&
        rtSub.chId === chId &&
        rtSub.cLen === changesLen &&
        rtSub.sLen === stacksLen
      ) {
        return rtSub.tab;
      }
      let tabRes = "chain";
      if (getPgSubmittedTogetherPeers(c).length > 1) tabRes = "submitted";
      else if (getPgTopicPeers(c).length > 0) tabRes = "topic";
      else if (getPgCherryPickPeers(c).length > 0) tabRes = "cherrypicks";
      const wSub = c._relTabSub || (c._relTabSub = {});
      wSub.num = num;
      wSub.stId = stId;
      wSub.status = c.status;
      wSub.merged = c.is_merged;
      wSub.aband = c.is_abandoned;
      wSub.topic = c.topic;
      wSub.subLen = subLen;
      wSub.chId = chId;
      wSub.cLen = changesLen;
      wSub.sLen = stacksLen;
      wSub.tab = tabRes;
      return tabRes;
    }
    if (saved) {
      if (saved === "submitted" && getPgSubmittedTogetherPeers(c).length > 1) return "submitted";
      if (saved === "topic" && getPgTopicPeers(c).length > 0) return "topic";
      if (saved === "cherrypicks" && getPgCherryPickPeers(c).length > 0) return "cherrypicks";
    }
    if (hasStack) return "chain";
    if (getPgSubmittedTogetherPeers(c).length > 1) return "submitted";
    if (getPgTopicPeers(c).length > 0) return "topic";
    if (getPgCherryPickPeers(c).length > 0) return "cherrypicks";
    return "chain";
  }

  function setPgRelatedChangesTab(clNum, tab) {
    const num = Number(clNum || 0);
    if (!num) return;
    if (!state.relatedChangesTabByCl) state.relatedChangesTabByCl = {};
    state.relatedChangesTabByCl[num] = tab;
    const slot = document.getElementById(`pgRelationSlot-${num}`);
    if (slot) {
      const c = (state.changes || []).find(x => Number(x.number) === num) || { number: num };
      slot.innerHTML = renderPgRelationChainHtml(c);
    }
  }

  function togglePgRelatedChangesTab(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const curTab = getPgRelatedChangesTab(num);
    const c = (state.changes || []).find(x => Number(x.number) === num) || { number: num };
    const st = findPgStackForClOrId(num, state.selectedStackId);
    const hasStack = Boolean(st && Array.isArray(st.commits) && st.commits.length > 1);
    const hasSubmitted = getPgSubmittedTogetherPeers(c).length > 1;
    const hasTopic = getPgTopicPeers(c).length > 0;
    const hasCherry = getPgCherryPickPeers(c).length > 0;
    const tabs = [];
    if (hasStack) tabs.push("chain");
    if (hasSubmitted) tabs.push("submitted");
    if (hasTopic) tabs.push("topic");
    if (hasCherry) tabs.push("cherrypicks");
    if (tabs.length <= 1) return;
    const idx = tabs.indexOf(curTab);
    const nextTab = tabs[(idx + 1) % tabs.length];
    setPgRelatedChangesTab(num, nextTab);
    const labelMap = {
      chain: "Relation chain",
      submitted: "Submitted together",
      topic: "Same topic",
      cherrypicks: "Cherry-picks",
    };
    if (typeof showToast === "function") {
      showToast(`Related changes: ${labelMap[nextTab] || nextTab} (Shortcut: Alt+R)`, 1800);
    }
  }

  function onPgRelationItemKeyDown(event, pNum, stackId) {
    if (!event) return;
    const key = event.key;
    const num = Number(pNum);
    const curNum = Number(state.selectedClNumber || num);
    const peer = (state.changes || []).find(ch => Number(ch.number) === num) || { number: num };

    if (key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (state.checkedClNumbers && state.checkedClNumbers.size > 0) {
        if (typeof toggleClCheckbox === "function") {
          toggleClCheckbox(num, event);
        } else if (state.checkedClNumbers) {
          if (state.checkedClNumbers.has(num)) {
            state.checkedClNumbers.delete(num);
          } else {
            state.checkedClNumbers.add(num);
          }
          if (typeof renderAll === "function") renderAll();
        }
        return;
      }
      if (state.continuousStackDiff) {
        scrollToStackCommit(num);
      } else if (stackId) {
        selectStackAndCl(stackId, num);
      } else {
        selectCl(num);
      }
      return;
    }
    if (key === "o") {
      event.preventDefault();
      if (state.continuousStackDiff) {
        scrollToStackCommit(num);
      } else if (stackId) {
        selectStackAndCl(stackId, num);
      } else {
        selectCl(num);
      }
      return;
    }
    if (key === "Enter" && event.shiftKey) {
      event.preventDefault();
      if (typeof togglePgStackCommitReviewed === "function") {
        togglePgStackCommitReviewed(num);
      }
      return;
    }
    if (key === "ArrowDown" || key === "j") {
      event.preventDefault();
      focusPgRelationItem("next", event.currentTarget);
      return;
    }
    if (key === "ArrowUp" || key === "k") {
      event.preventDefault();
      focusPgRelationItem("prev", event.currentTarget);
      return;
    }
    if (key === "Home") {
      event.preventDefault();
      focusPgRelationItem("first", event.currentTarget);
      return;
    }
    if (key === "End") {
      event.preventDefault();
      focusPgRelationItem("last", event.currentTarget);
      return;
    }
    if (key === "[" || key === "<" || (key === "ArrowLeft" && !event.altKey)) {
      event.preventDefault();
      if (typeof stepPgPatchset === "function") {
        stepPgPatchset(num, -1);
      }
      return;
    }
    if (key === "]" || key === ">" || (key === "ArrowRight" && !event.altKey)) {
      event.preventDefault();
      if (typeof stepPgPatchset === "function") {
        stepPgPatchset(num, 1);
      }
      return;
    }
    if (key === "x" || key === "X") {
      event.preventDefault();
      if (typeof toggleClCheckbox === "function") {
        toggleClCheckbox(num, event);
      } else if (state.checkedClNumbers) {
        if (state.checkedClNumbers.has(num)) {
          state.checkedClNumbers.delete(num);
        } else {
          state.checkedClNumbers.add(num);
        }
        if (typeof renderAll === "function") renderAll();
      }
      return;
    }
    if (key === "*" || key === "s" || key === "S") {
      event.preventDefault();
      if (typeof toggleStar === "function") {
        toggleStar(num);
      }
      return;
    }
    if (key === "p") {
      event.preventDefault();
      try {
        if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(`ag/${num}`).catch(() => {});
        }
      } catch (_) {}
      showToast(`Copied change ag/${num} to clipboard ✓`, 1500);
      return;
    }
    if (key === "c" || key === "C") {
      event.preventDefault();
      const peerCid = (typeof getPgChangeId === "function") ? getPgChangeId(peer) : (peer.change_id || "");
      if (peerCid) {
        try {
          if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(peerCid).catch(() => {});
          }
        } catch (_) {}
        showToast(`Copied Change-Id (${peerCid.slice(0, 12)}…) to clipboard ✓`, 1800);
      }
      return;
    }
    if (key === "d" || key === "D") {
      event.preventDefault();
      if (typeof openPgDownloadDialog === "function") {
        openPgDownloadDialog(num);
      }
      return;
    }
    if (key === "u" || (key === "U" && !event.shiftKey) || (event.altKey && (key === "u" || key === "U")) || key === "r") {
      const peerRebaseStatus = (typeof getPgChangeRebaseStatus === "function") ? getPgChangeRebaseStatus(peer) : null;
      if (peerRebaseStatus && (peerRebaseStatus.needsRebase || num === curNum)) {
        event.preventDefault();
        if (typeof onPgRelationRebaseClick === "function") {
          onPgRelationRebaseClick(event, num, peerRebaseStatus.parentClNumber || null);
        }
        return;
      }
    }
    if (key === "b" || key === "B") {
      event.preventDefault();
      const peerSel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
      const peerBase = String(peerSel.base || "base");
      if (peerBase !== "base" && peerBase !== "0") {
        const resetFn = (typeof window !== "undefined" && typeof window.resetPgPatchsetComparisonToBase === "function")
          ? window.resetPgPatchsetComparisonToBase
          : resetPgPatchsetComparisonToBase;
        resetFn(num);
      } else {
        const toggleFn = (typeof window !== "undefined" && typeof window.togglePgDiffPreviousPatchset === "function")
          ? window.togglePgDiffPreviousPatchset
          : togglePgDiffPreviousPatchset;
        toggleFn(num);
      }
      return;
    }
    if (key === "i" || key === "I") {
      event.preventDefault();
      const toggleStackFn = (typeof window !== "undefined" && typeof window.togglePgStackInterdiff === "function")
        ? window.togglePgStackInterdiff
        : togglePgStackInterdiff;
      toggleStackFn(stackId || state.selectedStackId);
      return;
    }
    if ((key === "r" || key === "R") && event.altKey) {
      event.preventDefault();
      togglePgRelatedChangesTab(curNum);
      return;
    }
  }

  function focusPgRelationItem(direction, currentEl) {
    const list = currentEl ? currentEl.closest(".pg-relation-list") : document.querySelector(".pg-relation-list");
    if (!list) return;
    const items = Array.from(list.querySelectorAll(".pg-relation-item[tabindex='0']"));
    if (!items.length) return;
    let idx = items.indexOf(currentEl);
    if (direction === "next") {
      idx = (idx >= 0 && idx < items.length - 1) ? idx + 1 : idx;
    } else if (direction === "prev") {
      idx = (idx > 0) ? idx - 1 : 0;
    } else if (direction === "first") {
      idx = 0;
    } else if (direction === "last") {
      idx = items.length - 1;
    }
    const target = items[idx];
    if (target && typeof target.focus === "function") {
      target.focus();
    }
  }

  function applyPgTopicFilter(topicName, preferredClNum = 0) {
    const t = String(topicName || "").trim();
    if (!t) return;
    const q = `topic:"${t}"`;
    state.searchQuery = q;
    state.authorFilter = "all";
    const prefNum = Number(preferredClNum || 0);
    if (prefNum > 0) {
      state.selectedClNumber = prefNum;
      const st = findPgStackForClOrId(prefNum, state.selectedStackId);
      if (st && st.stack_id) {
        state.selectedStackId = st.stack_id;
      }
    }
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = q;
    syncSearchClearButton();
    renderPgSearchPresetsDom();
    renderAll();
    showToast(`Filtered dashboard by topic: "${t}" ✓`, 2000);
  }

  function copyPgTopic(topicName) {
    const t = String(topicName || "").trim();
    if (!t) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t).catch(() => {});
      }
    } catch (e) {}
    showToast(`Copied topic "${t}" to clipboard ✓`, 2000);
  }

  function filterDashboardByBranch(branchName) {
    const b = String(branchName || "").trim();
    if (!b) return;
    const cleanBranch = b.replace(/^refs\/heads\//, "");
    const q = `branch:"${cleanBranch}"`;
    state.searchQuery = q;
    state.authorFilter = "all";
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = q;
    if (typeof syncSearchClearButton === "function") syncSearchClearButton();
    if (typeof renderPgSearchPresetsDom === "function") renderPgSearchPresetsDom();
    if (typeof renderAll === "function") renderAll();
    if (typeof showToast === "function") {
      showToast(`Filtered dashboard by branch: "${cleanBranch}" ✓`, 2000);
    }
  }

  function copyPgBranch(branchName) {
    const b = String(branchName || "").trim().replace(/^refs\/heads\//, "");
    if (!b) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(b).catch(() => {});
      }
    } catch (_) {}
    if (typeof showToast === "function") {
      showToast(`Copied branch "${b}" to clipboard ✓`, 2000);
    }
  }

  function copyPgBaseCommitSha(sha) {
    const s = String(sha || "").trim();
    if (!s) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(s).catch(() => {});
      }
    } catch (_) {}
    const shortSha = s.slice(0, 8);
    if (typeof showToast === "function") {
      showToast(`Copied base commit SHA (${shortSha}) to clipboard ✓`, 2000);
    }
  }

  function onPgRelationBaseItemKeyDown(event, targetBranch, parentSha) {
    if (!event) return;
    const key = event.key;
    if (key === "Enter" || key === " ") {
      event.preventDefault();
      filterDashboardByBranch(targetBranch);
      return;
    }
    if (key === "c" || key === "C") {
      event.preventDefault();
      copyPgBranch(targetBranch);
      return;
    }
    if (key === "s" || key === "S") {
      if (parentSha) {
        event.preventDefault();
        copyPgBaseCommitSha(parentSha);
        return;
      }
    }
    if (key === "ArrowDown" || key === "j") {
      event.preventDefault();
      focusPgRelationItem("first", event.currentTarget);
      return;
    }
    if (key === "ArrowUp" || key === "k") {
      event.preventDefault();
      focusPgRelationItem("prev", event.currentTarget);
      return;
    }
    if (key === "Home") {
      event.preventDefault();
      focusPgRelationItem("first", event.currentTarget);
      return;
    }
    if (key === "End") {
      event.preventDefault();
      focusPgRelationItem("last", event.currentTarget);
      return;
    }
  }

  function filterDashboardByHashtag(tag) {
    if (!tag) return;
    const cleanTag = String(tag).trim().replace(/^#/, "");
    applyPgHashtagFilter(cleanTag);
  }

  function applyPgHashtagFilter(tag) {
    const cleanTag = String(tag || "").trim().replace(/^#/, "");
    if (!cleanTag) return;
    const q = `hashtag:"${cleanTag}"`;
    state.searchQuery = q;
    state.authorFilter = "all";
    const inp = document.getElementById("queueSearchInput");
    if (inp) inp.value = q;
    syncSearchClearButton();
    renderPgSearchPresetsDom();
    renderAll();
    showToast(`Filtered dashboard by hashtag: #${cleanTag} ✓`, 2000);
  }

  function copyPgHashtag(tag) {
    const cleanTag = String(tag || "").trim().replace(/^#/, "");
    if (!cleanTag) return;
    const fullTag = `#${cleanTag}`;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(fullTag).catch(() => {});
      }
    } catch (_) {}
    if (typeof showToast === "function") {
      showToast(`Copied hashtag ${fullTag} to clipboard ✓`);
    }
  }

  function copyPgTrailerValue(event, btnOrVal, keyLabel = "") {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    let val = "";
    let key = keyLabel || "trailer";
    let btnEl = null;
    if (btnOrVal && typeof btnOrVal === "object" && btnOrVal.getAttribute) {
      btnEl = btnOrVal;
      val = btnEl.getAttribute("data-trailer-val") || "";
      key = btnEl.getAttribute("data-trailer-key") || key;
    } else if (typeof btnOrVal === "string") {
      val = btnOrVal;
    }

    if (!val) return;

    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(val).catch(() => {});
      } else if (typeof document !== "undefined") {
        const ta = document.createElement("textarea");
        ta.value = val;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (_) {}
        document.body.removeChild(ta);
      }
    } catch (_) {}

    if (btnEl) {
      btnEl.classList.add("is-copied");
      const origText = btnEl.textContent;
      btnEl.textContent = "✓";
      setTimeout(() => {
        btnEl.classList.remove("is-copied");
        btnEl.textContent = origText;
      }, 1200);
    }

    if (typeof showToast === "function") {
      showToast(`Copied ${key}: ${val.length > 40 ? val.slice(0, 37) + "…" : val} ✓`);
    }
  }

  function filterDashboardByChangeId(changeId) {
    if (!changeId) return;
    const cleanId = String(changeId).trim();
    const q = `change:${cleanId}`;
    if (typeof setQueueSearchAndApply === "function") {
      setQueueSearchAndApply(q);
    } else {
      state.searchQuery = q;
      const inp = document.getElementById("queueSearchInput");
      if (inp) inp.value = q;
      if (typeof syncSearchClearButton === "function") syncSearchClearButton();
      if (typeof renderPgSearchPresetsDom === "function") renderPgSearchPresetsDom();
      if (typeof renderAll === "function") renderAll();
    }
    if (typeof showToast === "function") {
      showToast(`Filtered dashboard by change:${cleanId.slice(0, 10)}… ✓`, 2000);
    }
  }

  function onPgChangeIdLinkClick(event, changeId) {
    if (event && (event.ctrlKey || event.metaKey)) {
      return; // Allow new tab navigation to android-review.googlesource.com/q/<changeId>
    }
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    if (event && typeof event.stopPropagation === "function") {
      event.stopPropagation();
    }
    if (!changeId) return;

    // Check if changeId exists in state.commits or state.changes
    const commits = state.commits || state.changes || [];
    const foundCl = commits.find(c =>
      c.change_id === changeId ||
      String(c.commit_message || "").includes(changeId)
    );

    if (foundCl && typeof selectCommit === "function") {
      selectCommit(foundCl.number);
      if (typeof showToast === "function") {
        showToast(`Jumped to change ag/${foundCl.number} (${changeId.slice(0, 10)}…) ✓`);
      }
      return;
    }

    // Otherwise apply search query to filter dashboard
    filterDashboardByChangeId(changeId);
  }

  function onPgAuthorEmailClick(event, email) {
    if (event && (event.ctrlKey || event.metaKey)) {
      return; // Allow default mailto: launch
    }
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    if (event && typeof event.stopPropagation === "function") {
      event.stopPropagation();
    }
    if (!email) return;

    const q = `reviewer:${email}`;
    if (typeof setQueueSearchAndApply === "function") {
      setQueueSearchAndApply(q);
    } else {
      state.searchQuery = q;
      const inp = document.getElementById("queueSearchInput");
      if (inp) inp.value = q;
      if (typeof syncSearchClearButton === "function") syncSearchClearButton();
      if (typeof renderPgSearchPresetsDom === "function") renderPgSearchPresetsDom();
      if (typeof renderAll === "function") renderAll();
    }
    if (typeof showToast === "function") {
      showToast(`Filtered dashboard for reviewer:${email} (Ctrl+Click to send email)`);
    }
  }

  function addPgHashtag(clNum, rawTag) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const cleanTag = String(rawTag || "").trim().replace(/^#/, "");
    if (!num || !cleanTag) return;
    const c = (state.changes || []).find(x => Number(x.number) === num);
    if (c) {
      if (!Array.isArray(c.hashtags)) c.hashtags = [];
      if (!c.hashtags.includes(cleanTag)) {
        c.hashtags.push(cleanTag);
        if (typeof showToast === "function") {
          showToast(`Added hashtag #${cleanTag} ✓`);
        }
        rerenderReviewersPanelDom(num);
      }
    }
  }

  function removePgHashtag(clNum, rawTag) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const cleanTag = String(rawTag || "").trim().replace(/^#/, "");
    if (!num || !cleanTag) return;
    const c = (state.changes || []).find(x => Number(x.number) === num);
    if (c && Array.isArray(c.hashtags)) {
      c.hashtags = c.hashtags.filter(t => t !== cleanTag);
      if (typeof showToast === "function") {
        showToast(`Removed hashtag #${cleanTag}`);
      }
      rerenderReviewersPanelDom(num);
    }
  }

  function showPgEditTopicInput(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const wrap = document.getElementById(`pgEditTopicWrap-${num}`);
    const chip = document.getElementById(`pgTopicChip-${num}`);
    const btnAdd = document.getElementById(`btnShowAddTopic-${num}`);
    const btnEdit = document.getElementById(`btnShowEditTopic-${num}`);
    const inp = document.getElementById(`pgEditTopicInput-${num}`);
    if (wrap) wrap.style.display = "inline-flex";
    if (chip) chip.style.display = "none";
    if (btnAdd) btnAdd.style.display = "none";
    if (btnEdit) btnEdit.style.display = "none";
    if (inp) {
      inp.focus();
      inp.select();
    }
  }

  function hidePgEditTopicInput(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const wrap = document.getElementById(`pgEditTopicWrap-${num}`);
    const chip = document.getElementById(`pgTopicChip-${num}`);
    const btnAdd = document.getElementById(`btnShowAddTopic-${num}`);
    const btnEdit = document.getElementById(`btnShowEditTopic-${num}`);
    const inp = document.getElementById(`pgEditTopicInput-${num}`);
    if (wrap) wrap.style.display = "none";
    if (chip) chip.style.display = "inline-flex";
    if (btnAdd) btnAdd.style.display = "";
    if (btnEdit) btnEdit.style.display = "";
    const c = (state.changes || []).find(x => Number(x.number) === num);
    if (inp && c) {
      inp.value = c.topic || "";
    }
  }

  function onPgEditTopicKeyDown(event, clNum) {
    if (event.key === "Enter") {
      event.preventDefault();
      submitPgEditTopic(clNum);
    } else if (event.key === "Escape") {
      event.preventDefault();
      hidePgEditTopicInput(clNum);
    }
  }

  async function submitPgEditTopic(clNum, explicitTopic = null) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const inp = document.getElementById(`pgEditTopicInput-${num}`);
    const rawVal = explicitTopic !== null ? explicitTopic : (inp ? inp.value : "");
    const cleanTopic = String(rawVal || "").trim();
    const c = (state.changes || []).find(x => Number(x.number) === num);
    const oldTopic = c ? String(c.topic || "").trim() : "";
    if (cleanTopic === oldTopic) {
      hidePgEditTopicInput(num);
      return;
    }
    if (c) {
      c.topic = cleanTopic;
    }
    (state.stacks || []).forEach(st => {
      (st.commits || []).forEach(cm => {
        if (Number(cm.number) === num) {
          cm.topic = cleanTopic;
        }
      });
    });
    rerenderReviewersPanelDom(num);
    syncPgRelationSlots(num);
    if (typeof showToast === "function") {
      if (cleanTopic) {
        showToast(`Topic updated: "${cleanTopic}" ✓`);
      } else {
        showToast("Topic removed ✓");
      }
    }
    try {
      const payload = {
        cl: num,
        topic: cleanTopic,
        viewer: state.viewer || "",
      };
      const headers = { "Content-Type": "application/json" };
      if (state.ui_token) {
        headers["X-Sidecar-Token"] = state.ui_token;
      }
      const doFetch = typeof sidecarFetch === "function" ? sidecarFetch : fetch;
      await doFetch("/api/gerrit/topic", {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
    } catch (_e) {
      // Optimistic update retained
    }
  }

  function removePgTopic(clNum) {
    submitPgEditTopic(clNum, "");
  }

  function showPgAddHashtagInput(clNum) {
    const wrap = document.getElementById(`pgAddHashtagWrap-${clNum}`);
    const btn = document.getElementById(`btnShowAddHashtag-${clNum}`);
    if (wrap) {
      wrap.style.display = "inline-flex";
      if (btn) btn.style.display = "none";
      const inp = document.getElementById(`pgAddHashtagInput-${clNum}`);
      if (inp) {
        inp.value = "";
        inp.focus();
      }
    }
  }

  function hidePgAddHashtagInput(clNum) {
    const wrap = document.getElementById(`pgAddHashtagWrap-${clNum}`);
    const btn = document.getElementById(`btnShowAddHashtag-${clNum}`);
    if (wrap) wrap.style.display = "none";
    if (btn) btn.style.display = "";
  }

  function submitPgAddHashtag(clNum) {
    const inp = document.getElementById(`pgAddHashtagInput-${clNum}`);
    if (inp && inp.value.trim()) {
      addPgHashtag(clNum, inp.value.trim());
    }
    hidePgAddHashtagInput(clNum);
  }

  function onPgAddHashtagKeyDown(event, clNum) {
    if (event.key === "Enter") {
      event.preventDefault();
      submitPgAddHashtag(clNum);
    } else if (event.key === "Escape") {
      event.preventDefault();
      hidePgAddHashtagInput(clNum);
    }
  }

  async function quickRebaseOnParent(clNum, parentClNum = null) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number));
    if (!num) return;
    const parentNum = parentClNum ? Number(parentClNum) : null;
    const targetDesc = parentNum ? `ag/${parentNum}` : "parent branch";
    if (typeof showToast === "function") {
      showToast(`Rebasing ag/${num} on top of ${targetDesc}…`, 2500);
    }

    try {
      if (typeof sidecarFetch !== "function") {
        if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
        return;
      }
      const resp = await sidecarFetch("/api/gerrit/rebase", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          base: parentNum ? String(parentNum) : null,
          allow_conflicts: false,
          viewer: state.viewer,
        }),
      });
      if (resp && resp.ok) {
        const data = await resp.json();
        if (data && data.status === "error") {
          if (typeof showToast === "function") {
            showToast(`Rebase failed: ${data.error || "Conflict detected"}. Opening rebase dialog…`, 4000);
          }
          if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
        } else {
          if (typeof showToast === "function") {
            showToast(`✓ ag/${num} successfully rebased on ${targetDesc}!`, 3500);
          }
          if (typeof fetchQueue === "function") {
            await fetchQueue({ keepSelection: true });
          }
        }
      } else {
        if (typeof showToast === "function") {
          showToast("Rebase request failed. Opening rebase dialog…", 3500);
        }
        if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
      }
    } catch (err) {
      if (typeof showToast === "function") {
        showToast(`Rebase error: ${err.message || err}`, 3500);
      }
      if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
    }
  }

  async function quickRebaseStackChain(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number));
    if (!num) return;
    if (typeof showToast === "function") {
      showToast(`Rebasing entire relation chain for ag/${num}…`, 2500);
    }

    try {
      if (typeof sidecarFetch !== "function") {
        if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
        return;
      }
      const resp = await sidecarFetch("/api/gerrit/rebase", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          rebase_chain: true,
          allow_conflicts: false,
          viewer: state.viewer,
        }),
      });
      if (resp && resp.ok) {
        const data = await resp.json();
        if (data && data.status === "error") {
          if (typeof showToast === "function") {
            showToast(`Rebase chain failed: ${data.error || "Conflict detected"}. Opening rebase dialog…`, 4000);
          }
          if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
        } else {
          if (typeof showToast === "function") {
            showToast(`✓ Relation chain for ag/${num} successfully rebased!`, 3500);
          }
          if (typeof fetchQueue === "function") {
            await fetchQueue({ keepSelection: true });
          }
        }
      } else {
        if (typeof showToast === "function") {
          showToast("Rebase chain request failed. Opening rebase dialog…", 3500);
        }
        if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
      }
    } catch (err) {
      if (typeof showToast === "function") {
        showToast(`Rebase chain error: ${err.message || err}`, 3500);
      }
      if (typeof openPgRebaseDialog === "function") openPgRebaseDialog(num);
    }
  }

  async function onPgRelationRebaseClick(event, clNum, parentClNum) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = Number(clNum);
    if (!num) return;
    if (event && event.altKey) {
      await quickRebaseOnParent(num, parentClNum);
    } else {
      const openFn = (typeof window !== "undefined" && typeof window.openPgRebaseDialog === "function")
        ? window.openPgRebaseDialog
        : (typeof openPgRebaseDialog === "function" ? openPgRebaseDialog : null);
      if (typeof openFn === "function") {
        openFn(num);
      }
    }
  }

  function copyPgRelatedCls(clNum, tab) {
    const num = Number(clNum || state.selectedClNumber || 0);
    const c = (state.changes || []).find(x => Number(x.number) === num) || { number: num };
    let nums = [];
    if (tab === "submitted") {
      const peers = getPgSubmittedTogetherPeers(c);
      nums = peers.map(p => Number(p.number));
    } else if (tab === "topic") {
      const peers = getPgTopicPeers(c);
      nums = peers.map(p => Number(p.number));
    } else if (tab === "cherrypicks") {
      const group = (typeof getPgCherryPickGroup === "function") ? getPgCherryPickGroup(c) : getPgCherryPickPeers(c);
      nums = group.map(p => Number(p.number));
    } else {
      const st = findPgStackForClOrId(num, state.selectedStackId);
      if (st && Array.isArray(st.commits)) {
        nums = st.commits.map(p => Number(p.number));
      }
    }
    if (!nums.length && num) nums = [num];
    const text = nums.join(", ");
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(() => {});
      }
    } catch (e) {}
    showToast(`Copied ${nums.length} change numbers (${text}) ✓`, 2500);
  }

  function renderPgRelVoteChip(peer) {
    return AemuGerritReact.renderPgRelVoteChip(peer, getPgReactBridge());
  }

  const _PG_EMPTY_REBASE_STATUS = Object.freeze({
    hasConflict: false,
    isOutdated: false,
    isIndirectlyOutdated: false,
    isParentMerged: false,
    needsRebase: false,
    parentCommit: null,
    parentClNumber: null,
    parentSubject: "",
    parentCurrentPs: 1,
    reason: "",
    conflicts: _PG_EMPTY_ARRAY,
  });

  function computeStackRebaseRevisionSig(commits) {
    let h = (FNV1A_OFFSET_BASIS ^ commits.length) | 0;
    for (let i = 0; i < commits.length; i++) {
      const item = commits[i];
      if (!item) continue;
      const rSub = item._rebSub || (item._rebSub = {});
      if (
        rSub.hash === undefined ||
        rSub.num !== item.number ||
        rSub.ps !== item.patch_set ||
        rSub.parentPs !== item.parent_patch_set ||
        rSub.curRev !== item.current_revision ||
        rSub.parentRev !== item.parent_revision ||
        rSub.status !== item.status ||
        rSub.merged !== item.is_merged
      ) {
        rSub.num = item.number;
        rSub.ps = item.patch_set;
        rSub.parentPs = item.parent_patch_set;
        rSub.curRev = item.current_revision;
        rSub.parentRev = item.parent_revision;
        rSub.status = item.status;
        rSub.merged = item.is_merged;
        let ih = Math.imul((FNV1A_OFFSET_BASIS ^ (item.number | 0)), FNV1A_PRIME);
        ih = Math.imul(ih ^ ((item.patch_set || 1) | 0), FNV1A_PRIME);
        ih = fnv1aHashStr(ih, String(item.parent_patch_set || ""));
        ih = fnv1aHashStr(ih, item.current_revision);
        ih = fnv1aHashStr(ih, item.parent_revision);
        ih = fnv1aHashStr(ih, item.status);
        rSub.hash = Math.imul(ih ^ (item.is_merged ? 1 : 0), FNV1A_PRIME) | 0;
      }
      h = Math.imul(h ^ rSub.hash, FNV1A_PRIME) | 0;
    }
    if (commits._cachedRebRevHash === h && typeof commits._cachedRebRevSig === "string") {
      return commits._cachedRebRevSig;
    }
    const sig = String(h >>> 0);
    commits._cachedRebRevHash = h;
    commits._cachedRebRevSig = sig;
    return sig;
  }

  function getPgChangeRebaseStatus(c, bundle = null) {
    if (!c) {
      return _PG_EMPTY_REBASE_STATUS;
    }
    const num = Number(c.number);
    const conflictFiles = (bundle && Array.isArray(bundle.conflicts) && bundle.conflicts.length > 0)
      ? bundle.conflicts
      : (Array.isArray(c.conflicts) ? c.conflicts : _PG_EMPTY_ARRAY);
    const hasConflict = isClConflict(c, bundle);
    const st = (!c.is_merged && !c.is_abandoned && c.status !== "MERGED" && c.status !== "ABANDONED" && typeof findPgStackForClOrId === "function")
      ? (c._renderPassStack !== undefined ? c._renderPassStack : findPgStackForClOrId(num))
      : null;
    const hasMultiStack = Boolean(st && Array.isArray(st.commits) && st.commits.length > 1);
    const rebActEnabled = Boolean(c.actions && c.actions.rebase && c.actions.rebase.enabled);
    const bRebNeeded = Boolean(bundle && bundle.rebase_needed);
    const stRevSig = hasMultiStack
      ? computeStackRebaseRevisionSig(st.commits)
      : "";
    if (c._cachedRebaseStatus) {
      const rsSub = c._rebaseStatSub;
      if (
        rsSub &&
        rsSub.num === num &&
        rsSub.status === c.status &&
        rsSub.merged === Boolean(c.is_merged) &&
        rsSub.aband === Boolean(c.is_abandoned) &&
        rsSub.conflict === hasConflict &&
        rsSub.confLen === conflictFiles.length &&
        rsSub.branch === (c.branch || "") &&
        rsSub.rebaseable === Boolean(c.rebaseable) &&
        rsSub.outdated === Boolean(c.is_outdated) &&
        rsSub.rebNeeded === Boolean(c.rebase_needed) &&
        rsSub.bRebNeeded === bRebNeeded &&
        rsSub.rebAct === rebActEnabled &&
        rsSub.stRevSig === stRevSig
      ) {
        return c._cachedRebaseStatus;
      }
    }
    const stUpper = String(c.status || "").toUpperCase();
    const isMerged = stUpper === "MERGED" || Boolean(c.is_merged);
    const isAbandoned = stUpper === "ABANDONED" || Boolean(c.is_abandoned);
    const rebaseSig = `${num}|${stUpper}|${isMerged ? 1 : 0}|${isAbandoned ? 1 : 0}|${hasConflict ? 1 : 0}|${conflictFiles.length}|${c.branch || ""}|${c.rebaseable ? 1 : 0}|${c.is_outdated ? 1 : 0}|${c.rebase_needed ? 1 : 0}|${bRebNeeded ? 1 : 0}|${rebActEnabled ? 1 : 0}|${stRevSig}`;
    if (c._cachedRebaseStatusSig === rebaseSig && c._cachedRebaseStatus) {
      c._rebaseStatSub = { num, status: c.status, merged: Boolean(c.is_merged), aband: Boolean(c.is_abandoned), conflict: hasConflict, confLen: conflictFiles.length, branch: c.branch || "", rebaseable: Boolean(c.rebaseable), outdated: Boolean(c.is_outdated), rebNeeded: Boolean(c.rebase_needed), bRebNeeded, rebAct: rebActEnabled, stRevSig };
      return c._cachedRebaseStatus;
    }

    let isOutdated = false;
    let isIndirectlyOutdated = false;
    let isParentMerged = false;
    let parentCommit = null;
    let parentClNumber = null;
    let parentSubject = "";
    let parentCurrentPs = 1;
    let reason = "";

    if (st && Array.isArray(st.commits) && st.commits.length > 1) {
      const idx = st.commits.findIndex(item => Number(item && item.number) === num);
      if (idx > 0 && st.commits[idx - 1]) {
        parentCommit = st.commits[idx - 1];
        parentClNumber = Number(parentCommit.number);
        parentSubject = parentCommit.subject || "";
        parentCurrentPs = Number(parentCommit.patch_set || 1);
        const pStUpper = String(parentCommit.status || "").toUpperCase();
        if (pStUpper === "MERGED" || parentCommit.is_merged) {
          isParentMerged = true;
          reason = `Parent change ag/${parentClNumber} was merged into ${c.branch || "target branch"}`;
        } else if (parentCommit.current_revision && c.parent_revision && parentCommit.current_revision !== c.parent_revision) {
          isOutdated = true;
          reason = `Parent change ag/${parentClNumber} has updated revision (PS ${parentCurrentPs})`;
        } else if (parentCommit.patch_set && c.parent_patch_set && Number(parentCommit.patch_set) > Number(c.parent_patch_set)) {
          isOutdated = true;
          reason = `Parent change ag/${parentClNumber} has newer patchset (PS ${parentCurrentPs})`;
        }

        if (!isOutdated && !isParentMerged) {
          for (let aIdx = idx - 2; aIdx >= 0; aIdx--) {
            const ancestor = st.commits[aIdx];
            if (ancestor) {
              const aNum = Number(ancestor.number);
              const aStatus = String(ancestor.status || "").toUpperCase();
              const aMerged = aStatus === "MERGED" || Boolean(ancestor.is_merged);
              const childOfAncestor = st.commits[aIdx + 1];
              const aPs = Number(ancestor.patch_set || 1);
              const childParentPs = Number((childOfAncestor && childOfAncestor.parent_patch_set) || 1);
              const aRevMismatch = Boolean(ancestor.current_revision && childOfAncestor && childOfAncestor.parent_revision && ancestor.current_revision !== childOfAncestor.parent_revision);
              if (aMerged || aPs > childParentPs || aRevMismatch) {
                isOutdated = true;
                isIndirectlyOutdated = true;
                reason = `Ancestor change ag/${aNum} in relation chain was ${aMerged ? "merged" : "updated"} (rebase chain recommended)`;
                break;
              }
            }
          }
        }
      }
    }

    if (!isMerged && !isAbandoned && !isOutdated && !isParentMerged) {
      const isRebaseable = Boolean(
        c.rebaseable === true ||
        (c.actions && c.actions.rebase && c.actions.rebase.enabled) ||
        c.is_outdated === true ||
        c.rebase_needed === true ||
        (bundle && bundle.rebase_needed)
      );
      if (isRebaseable) {
        isOutdated = true;
        const bName = (typeof getPgBranchDisplay === "function") ? getPgBranchDisplay(c.branch) : (c.branch || "base");
        reason = `Target branch ${bName || "base"} has newer commits (rebase recommended)`;
      }
    }

    if (hasConflict && !reason) {
      if (conflictFiles.length > 0) {
        reason = `Merge conflict with ${c.branch || "target branch"} in: ${conflictFiles.join(", ")}`;
      } else {
        reason = `Merge conflict with ${c.branch || "target branch"}`;
      }
    }

    const res = {
      hasConflict,
      isOutdated,
      isIndirectlyOutdated,
      isParentMerged,
      needsRebase: hasConflict || isOutdated || isParentMerged,
      parentCommit,
      parentClNumber,
      parentSubject,
      parentCurrentPs,
      reason,
      conflicts: conflictFiles,
    };
    c._cachedRebaseStatusSig = rebaseSig;
    c._cachedRebaseStatus = res;
    c._rebaseStatSub = { num, status: c.status, merged: Boolean(c.is_merged), aband: Boolean(c.is_abandoned), conflict: hasConflict, confLen: conflictFiles.length, branch: c.branch || "", rebaseable: Boolean(c.rebaseable), outdated: Boolean(c.is_outdated), rebNeeded: Boolean(c.rebase_needed), bRebNeeded, rebAct: rebActEnabled, stRevSig };
    return res;
  }

  function renderPgRelStatusTag(peer) {
    return AemuGerritReact.renderPgRelStatusTag(peer, getPgReactBridge());
  }

  function syncPgClMergeability(clNum, data) {
    const num = Number(clNum);
    if (!num || !data) return;
    if (typeof state === "undefined" || !state) return;
    state.mergeabilityByCl = state.mergeabilityByCl || {};
    state.mergeabilityByCl[num] = data;

    const updateTarget = (item) => {
      if (!item) return;
      if (typeof data.mergeable === "boolean") item.mergeable = data.mergeable;
      if (data.contains_git_conflicts || data.mergeable === false) item.contains_git_conflicts = true;
      if (Array.isArray(data.conflicts)) item.conflicts = data.conflicts;
      if (data.mergeable === false || data.contains_git_conflicts) {
        if (!Array.isArray(item.submit_requirements)) {
          item.submit_requirements = [];
        }
        const hasMergeReq = item.submit_requirements.some(r => r && (r.name === "Change-Is-Mergeable" || r.name === "Mergeable"));
        if (!hasMergeReq) {
          const cList = Array.isArray(data.conflicts) && data.conflicts.length > 0 ? ` (${data.conflicts.join(", ")})` : "";
          item.submit_requirements.push({
            name: "Change-Is-Mergeable",
            status: "UNSATISFIED",
            description: `Change cannot be merged cleanly onto target branch due to merge conflicts${cList}.`,
            expression: "is:mergeable",
            failing_atoms: [`Merge conflict detected with target branch${cList}`],
            passing_atoms: [],
          });
        }
      }
    };

    (state.changes || []).forEach(x => { if (Number(x && x.number) === num) updateTarget(x); });
    (state.queue || []).forEach(x => { if (Number(x && x.number) === num) updateTarget(x); });
    (state.stacks || []).forEach(st => {
      (st.commits || []).forEach(sc => { if (Number(sc && sc.number) === num) updateTarget(sc); });
    });
    if (state.diffsByCl && state.diffsByCl[num]) {
      updateTarget(state.diffsByCl[num]);
    }

    const clObj = (state.changes || []).find(x => Number(x && x.number) === num) ||
      (state.queue || []).find(x => Number(x && x.number) === num) ||
      (state.stacks || []).flatMap(s => s.commits || []).find(sc => Number(sc && sc.number) === num);
    const bundle = (state.diffsByCl && state.diffsByCl[num]) || null;

    if (clObj && typeof document !== "undefined" && document) {
      // 1. Header status badge & actions
      const hdrEl = document.getElementById(`pgChangeHeader-${num}`);
      if (hdrEl && typeof renderPgChangeHeaderHtml === "function") {
        hdrEl.outerHTML = renderPgChangeHeaderHtml(clObj, bundle);
      }
      // 2. Rebase notice banner
      const bannerEl = document.getElementById(`pgRebaseNoticeBanner-${num}`);
      const newBannerHtml = (typeof renderPgRebaseNoticeBannerHtml === "function")
        ? renderPgRebaseNoticeBannerHtml(clObj, bundle)
        : "";
      if (bannerEl) {
        if (newBannerHtml) {
          bannerEl.outerHTML = newBannerHtml;
        } else {
          bannerEl.remove();
        }
      } else if (newBannerHtml) {
        const parentSec = document.getElementById(`pgStackCommitBody-${num}`);
        if (parentSec) {
          const temp = document.createElement("div");
          temp.innerHTML = newBannerHtml;
          if (temp.firstElementChild) {
            const hdr = document.getElementById(`pgChangeHeader-${num}`);
            if (hdr && hdr.nextSibling) {
              parentSec.insertBefore(temp.firstElementChild, hdr.nextSibling);
            } else {
              parentSec.appendChild(temp.firstElementChild);
            }
          }
        }
      }
      // 3. Sidebar dashboard row status tag & card conflict classes
      const statusTagHtml = (typeof renderPgStatusTagHtml === "function") ? renderPgStatusTagHtml(clObj) : "";
      const statusSlotEl = document.getElementById(`pgStatusSlot-${num}`);
      if (statusSlotEl) {
        statusSlotEl.innerHTML = statusTagHtml;
      }
      const batchStatusSlotEl = document.getElementById(`pgBatchStatusSlot-${num}`);
      if (batchStatusSlotEl) {
        batchStatusSlotEl.innerHTML = statusTagHtml;
      }
      const hasConflict = (typeof isClConflict === "function") && isClConflict(clObj, bundle);
      document.querySelectorAll(`.cl-card[data-cl-num="${num}"]`).forEach(cardEl => {
        cardEl.classList.toggle("is-conflict", hasConflict);
        cardEl.classList.toggle("cl-card-conflict", hasConflict);
        if (hasConflict) {
          cardEl.classList.remove("cl-card-submittable");
        }
        if (!statusSlotEl) {
          const slot = cardEl.querySelector(".pg-status-slot");
          if (slot) slot.innerHTML = statusTagHtml;
        }
      });
      // 4. Relation chain slot: update all visible relation chains
      const relChains = document.querySelectorAll(`[id^="pgRelationSlot-"]`);
      relChains.forEach(relSlot => {
        const slotClNum = Number(relSlot.id.replace("pgRelationSlot-", ""));
        const parentCl = (state.changes || []).find(ch => Number(ch && ch.number) === slotClNum) || clObj;
        if (parentCl && typeof renderPgRelationChainHtml === "function") {
          relSlot.innerHTML = renderPgRelationChainHtml(parentCl);
        }
      });
      // 5. Submit requirements slot
      const srSlotEl = document.getElementById(`pgSrSlot-${num}`);
      if (srSlotEl && typeof renderPgSubmitRequirementsHtml === "function") {
        srSlotEl.innerHTML = renderPgSubmitRequirementsHtml(clObj);
      }
      // 6. File list table wrap (update conflict pills)
      const fileTableWrap = document.querySelector(`#pgFilesTabPane-${num} .pg-file-list-table-wrap`);
      if (fileTableWrap && typeof renderPgFileListHtml === "function") {
        fileTableWrap.outerHTML = renderPgFileListHtml(clObj, bundle);
      }
      // 7. Submit buttons state
      if (typeof syncSubmitClButtonsDom === "function") {
        syncSubmitClButtonsDom(getActiveCommitsForDiff());
      }
    }
  }

  async function fetchPgMergeable(clNum, patchSet = "current") {
    const num = Number(clNum);
    if (!num) return { mergeable: true, conflicts: [] };
    try {
      const resp = await sidecarFetch(`/api/gerrit/mergeable?cl=${num}&ps=${patchSet}`);
      if (resp.ok) {
        const data = await resp.json();
        if (data && typeof data.mergeable === "boolean") {
          syncPgClMergeability(num, data);
          return data;
        }
      }
    } catch (err) {
      console.warn(`[Fast Gerrit] Failed to query mergeable for ag/${num}:`, err);
    }
    return { mergeable: true, conflicts: [] };
  }

  async function loadPgMergeable(clNum, patchSet = "current") {
    const num = Number(clNum);
    if (!num) return;
    if (typeof state === "undefined" || !state) return;
    if (typeof window !== "undefined" && window.__GERRIT_UI_BOOTSTRAP__ && window.__GERRIT_UI_BOOTSTRAP__.changes) {
      return;
    }
    const targetCl = findPgChangeByNumber(num) ||
      (state.queue || []).find(x => Number(x && x.number) === num);
    if (targetCl && (isClMerged(targetCl) || isClAbandoned(targetCl))) return;
    const targetPs = patchSet === "current" ? Number((targetCl && targetCl.patch_set) || 1) : Number(patchSet || 1);
    if (
      patchSet === "current" &&
      state._loadedMergeablePsByCl &&
      state._loadedMergeablePsByCl[num] === targetPs
    ) {
      return;
    }
    state.inFlightMergeablePromises = state.inFlightMergeablePromises || {};
    if (state.inFlightMergeablePromises[num]) {
      return state.inFlightMergeablePromises[num];
    }

    const p = (async () => {
      try {
        await fetchPgMergeable(num, patchSet);
        if (patchSet === "current") {
          state._loadedMergeablePsByCl = state._loadedMergeablePsByCl || {};
          state._loadedMergeablePsByCl[num] = targetPs;
        }
      } finally {
        delete state.inFlightMergeablePromises[num];
      }
    })();
    state.inFlightMergeablePromises[num] = p;
    return p;
  }

  async function refreshPgMergeableStatus(clNum) {
    const num = Number(clNum);
    if (!num) return;
    showToast(`Checking mergeability for ag/${num}…`, 1500);
    const data = await fetchPgMergeable(num);
    const clObj = (state.changes || []).find(x => Number(x && x.number) === num) ||
      (state.queue || []).find(x => Number(x && x.number) === num);
    const bundle = (state.diffsByCl && state.diffsByCl[num]) || null;
    const hdrEl = document.getElementById(`pgChangeHeader-${num}`);
    if (hdrEl && clObj) {
      hdrEl.outerHTML = renderPgChangeHeaderHtml(clObj, bundle);
    }
    const bannerEl = document.getElementById(`pgRebaseNoticeBanner-${num}`);
    if (bannerEl && clObj) {
      const newBannerHtml = renderPgRebaseNoticeBannerHtml(clObj, bundle);
      if (newBannerHtml) {
        bannerEl.outerHTML = newBannerHtml;
      } else {
        bannerEl.remove();
      }
    } else if (clObj) {
      const parentSec = document.getElementById(`pgStackCommitBody-${num}`);
      const newBannerHtml = renderPgRebaseNoticeBannerHtml(clObj, bundle);
      if (parentSec && newBannerHtml) {
        const temp = document.createElement("div");
        temp.innerHTML = newBannerHtml;
        if (temp.firstElementChild) {
          const hdr = document.getElementById(`pgChangeHeader-${num}`);
          if (hdr && hdr.nextSibling) {
            parentSec.insertBefore(temp.firstElementChild, hdr.nextSibling);
          } else {
            parentSec.appendChild(temp.firstElementChild);
          }
        }
      }
    }
    if (data && data.mergeable === false) {
      const cList = Array.isArray(data.conflicts) && data.conflicts.length > 0
        ? ` (${data.conflicts.join(", ")})`
        : "";
      showToast(`⚠️ Merge conflict detected with target branch${cList}`, 4000);
    } else {
      showToast(`✓ Change ag/${num} is clean and mergeable without conflicts`, 3000);
    }
  }

  function renderPgRelBaseRowHtml(c, st) {
    return AemuGerritReact.renderPgRelBaseRowHtml(c, st, getPgReactBridge());
  }

  const _PG_EMPTY_REL_PS = Object.freeze({});

  function getCachedOrPersistedRelPatchset(sc, scNum) {
    const inMem = state.selectedPatchsetByCl && state.selectedPatchsetByCl[scNum];
    if (inMem) return inMem;
    if (!sc._checkedPersistedPs) {
      sc._checkedPersistedPs = true;
      sc._persistedRelPs = getPgSelectedPatchset(scNum) || _PG_EMPTY_REL_PS;
    }
    return sc._persistedRelPs || _PG_EMPTY_REL_PS;
  }

  function computeSingleCommitRelHash(sc) {
    const scNum = Number(sc.number || 0);
    const selPs = getCachedOrPersistedRelPatchset(sc, scNum);
    const scBundle = (state.diffsByCl && state.diffsByCl[scNum]) || null;
    const bConflicts = scBundle && Array.isArray(scBundle.conflicts) ? scBundle.conflicts.length : 0;
    const cConflicts = Array.isArray(sc.conflicts) ? sc.conflicts.length : 0;
    const isChk = Boolean(state.checkedClNumbers && state.checkedClNumbers.has(scNum));
    const isRev = isPgStackCommitReviewed(scNum);
    const isCol = Boolean(state.collapsedStackCommits && state.collapsedStackCommits.has(scNum));
    const srLen = Array.isArray(sc.submit_requirements) ? sc.submit_requirements.length : 0;
    const revs = Array.isArray(sc.reviewers_detail) ? sc.reviewers_detail : _PG_EMPTY_ARRAY;
    const revsLen = revs.length;
    const r0Vote = revs[0] ? (revs[0].cr_vote || 0) : 0;
    const doneCr2 = Array.isArray(sc.human_cr2) ? sc.human_cr2.length : Number(sc.human_cr2_count || 0);
    const psVal = selPs.ps || "";
    const baseVal = selPs.base || "";
    const bitMask =
      (sc.contains_git_conflicts ? 1 : 0) |
      (sc.presubmit_vetted ? 2 : 0) |
      (sc.rebaseable ? 4 : 0) |
      (sc.is_outdated ? 8 : 0) |
      (sc.rebase_needed ? 16 : 0) |
      (isChk ? 32 : 0) |
      (isRev ? 64 : 0) |
      (isCol ? 128 : 0);
    const relSub = sc._relSub || (sc._relSub = {});
    if (
      relSub.hash !== undefined &&
      relSub.scNum === scNum &&
      relSub.ps === sc.patch_set &&
      relSub.status === sc.status &&
      relSub.subject === sc.subject &&
      relSub.topic === sc.topic &&
      relSub.branch === sc.branch &&
      relSub.mergeable === sc.mergeable &&
      relSub.submittable === sc.submittable &&
      relSub.gerritSub === sc.gerrit_submittable &&
      relSub.psStatus === sc.presubmit_status &&
      relSub.psScore === sc.presubmit_score &&
      relSub.doneCr2 === doneCr2 &&
      relSub.revsLen === revsLen &&
      relSub.r0Vote === r0Vote &&
      relSub.curRev === sc.current_revision &&
      relSub.parentRev === sc.parent_revision &&
      relSub.parentPs === sc.parent_patch_set &&
      relSub.bConflicts === bConflicts &&
      relSub.cConflicts === cConflicts &&
      relSub.srLen === srLen &&
      relSub.psVal === psVal &&
      relSub.baseVal === baseVal &&
      relSub.bitMask === bitMask
    ) {
      return relSub.hash;
    }
    relSub.scNum = scNum;
    relSub.ps = sc.patch_set;
    relSub.status = sc.status;
    relSub.subject = sc.subject;
    relSub.topic = sc.topic;
    relSub.branch = sc.branch;
    relSub.mergeable = sc.mergeable;
    relSub.submittable = sc.submittable;
    relSub.gerritSub = sc.gerrit_submittable;
    relSub.psStatus = sc.presubmit_status;
    relSub.psScore = sc.presubmit_score;
    relSub.doneCr2 = doneCr2;
    relSub.revsLen = revsLen;
    relSub.r0Vote = r0Vote;
    relSub.curRev = sc.current_revision;
    relSub.parentRev = sc.parent_revision;
    relSub.parentPs = sc.parent_patch_set;
    relSub.bConflicts = bConflicts;
    relSub.cConflicts = cConflicts;
    relSub.srLen = srLen;
    relSub.psVal = psVal;
    relSub.baseVal = baseVal;
    relSub.bitMask = bitMask;
    const sigStr = `${scNum}:${sc.patch_set || 1}:${sc.status || ""}:${sc.subject || ""}:${sc.topic || ""}:${sc.branch || ""}:${sc.mergeable ?? ""}:${sc.submittable ?? ""}:${sc.gerrit_submittable ?? ""}:${sc.presubmit_status || ""}:${sc.presubmit_score || 0}:${doneCr2}:${revsLen}:${r0Vote}:${sc.current_revision || ""}:${sc.parent_revision || ""}:${sc.parent_patch_set || ""}:${bConflicts}:${cConflicts}:${srLen}:${psVal}:${baseVal}:${bitMask}`;
    relSub.hash = fnv1aHashStr(FNV1A_OFFSET_BASIS, sigStr);
    return relSub.hash;
  }

  function computePgStackRelationSignature(st, stackCommits) {
    let h = (FNV1A_OFFSET_BASIS ^ stackCommits.length) | 0;
    for (let i = 0; i < stackCommits.length; i++) {
      const sc = stackCommits[i];
      if (!sc) continue;
      h = Math.imul(h ^ computeSingleCommitRelHash(sc), FNV1A_PRIME) | 0;
    }
    const c0 = stackCommits[0];
    const c0Num = c0 ? (c0.number || 0) : 0;
    const c0Ps = c0 ? (c0.patch_set || 1) : 1;
    const c0Status = c0 ? (c0.status || "") : "";
    const scLen = stackCommits.length;
    const hUns = h >>> 0;
    if (st) {
      const rsSub = st._relSigSub;
      if (
        rsSub &&
        typeof st._cachedStackRelFullSig === "string" &&
        rsSub.c0Num === c0Num &&
        rsSub.c0Ps === c0Ps &&
        rsSub.c0Status === c0Status &&
        rsSub.scLen === scLen &&
        rsSub.hUns === hUns
      ) {
        return st._cachedStackRelFullSig;
      }
    } else if (c0) {
      const rsSub = c0._singleRelSigSub;
      if (
        rsSub &&
        typeof rsSub.sig === "string" &&
        rsSub.c0Num === c0Num &&
        rsSub.c0Ps === c0Ps &&
        rsSub.c0Status === c0Status &&
        rsSub.scLen === scLen &&
        rsSub.hUns === hUns
      ) {
        return rsSub.sig;
      }
    }
    const c0Prefix = c0 ? `${c0Num}:${c0Ps}:${c0Status}:` : "";
    const rawSig = `${c0Prefix}${scLen}:${hUns}`;
    if (st) {
      st._cachedStackRelRawSig = rawSig;
      st._cachedStackRelFullSig = rawSig;
      const wSub = st._relSigSub || (st._relSigSub = {});
      wSub.c0Num = c0Num;
      wSub.c0Ps = c0Ps;
      wSub.c0Status = c0Status;
      wSub.scLen = scLen;
      wSub.hUns = hUns;
    } else if (c0) {
      const wSub = c0._singleRelSigSub || (c0._singleRelSigSub = {});
      wSub.c0Num = c0Num;
      wSub.c0Ps = c0Ps;
      wSub.c0Status = c0Status;
      wSub.scLen = scLen;
      wSub.hUns = hUns;
      wSub.sig = rawSig;
    }
    return rawSig;
  }

  function renderPgRelationChainHtml(c) {
    if (!c) return "";
    const clNum = Number(c.number || 0);
    const st = (c._renderPassStack !== undefined) ? c._renderPassStack : findPgStackForClOrId(clNum, state.selectedStackId);
    const tab = getPgRelatedChangesTab(clNum, c, st);
    const hasMultiStack = Boolean(st && Array.isArray(st.commits) && st.commits.length > 1);
    const persistedInterdiff = (hasMultiStack && st.stack_id && getPgPersistedStackInterdiff(st.stack_id)) ? 1 : 0;
    if (hasMultiStack && tab === "chain" && persistedInterdiff && canPgStackInterdiff(st.commits) && !isPgStackInterdiffActive(st.commits, st.stack_id)) {
      ensurePgStackInterdiffRestored(st);
    }
    const subTogetherLen = Array.isArray(c.submitted_together) ? c.submitted_together.length : 0;
    const changesLen = Array.isArray(state.changes) ? state.changes.length : 0;
    const stacksLen = Array.isArray(state.stacks) ? state.stacks.length : 0;
    const chId = getPgChangeId(c);
    const stId = st ? (st.stack_id || "") : "";
    const checkedSize = state.checkedClNumbers ? state.checkedClNumbers.size : 0;
    const rcSub = c._relChainSub;
    const singleRelHash = hasMultiStack ? 0 : computeSingleCommitRelHash(c);
    if (
      !hasMultiStack &&
      rcSub &&
      typeof c._cachedRelationChainHtml === "string" &&
      rcSub.singleMode === true &&
      rcSub.clNum === clNum &&
      rcSub.stId === stId &&
      rcSub.selCl === state.selectedClNumber &&
      rcSub.selSt === state.selectedStackId &&
      rcSub.cont === state.continuousStackDiff &&
      rcSub.tab === tab &&
      rcSub.interdiff === persistedInterdiff &&
      rcSub.checkedSize === checkedSize &&
      rcSub.topic === c.topic &&
      rcSub.chId === chId &&
      rcSub.subLen === subTogetherLen &&
      rcSub.cLen === changesLen &&
      rcSub.sLen === stacksLen &&
      rcSub.singleHash === singleRelHash &&
      rcSub.ps === c.patch_set &&
      rcSub.status === c.status
    ) {
      return c._cachedRelationChainHtml;
    }
    const stackCommits = hasMultiStack ? st.commits : [c];
    const stackSig = computePgStackRelationSignature(hasMultiStack ? st : null, stackCommits);
    if (
      rcSub &&
      typeof c._cachedRelationChainHtml === "string" &&
      rcSub.clNum === clNum &&
      rcSub.stId === stId &&
      rcSub.selCl === state.selectedClNumber &&
      rcSub.selSt === state.selectedStackId &&
      rcSub.cont === state.continuousStackDiff &&
      rcSub.tab === tab &&
      rcSub.interdiff === persistedInterdiff &&
      rcSub.checkedSize === checkedSize &&
      rcSub.topic === c.topic &&
      rcSub.chId === chId &&
      rcSub.subLen === subTogetherLen &&
      rcSub.cLen === changesLen &&
      rcSub.sLen === stacksLen &&
      rcSub.stackSig === stackSig
    ) {
      return c._cachedRelationChainHtml;
    }
    const relCacheKey = `${clNum}|${stId}|${state.selectedClNumber || 0}|${state.selectedStackId || ""}|${state.continuousStackDiff ? 1 : 0}|${tab}|${persistedInterdiff}|${checkedSize}|${c.topic || ""}|${chId}|${subTogetherLen}|${changesLen}:${stacksLen}|${stackSig}`;
    const saveRelSub = (htmlVal) => {
      c._cachedRelationChainKey = relCacheKey;
      c._cachedRelationChainHtml = htmlVal;
      const wSub = c._relChainSub || (c._relChainSub = {});
      wSub.singleMode = !hasMultiStack;
      wSub.singleHash = singleRelHash;
      wSub.ps = c.patch_set;
      wSub.status = c.status;
      wSub.clNum = clNum;
      wSub.stId = stId;
      wSub.selCl = state.selectedClNumber;
      wSub.selSt = state.selectedStackId;
      wSub.cont = state.continuousStackDiff;
      wSub.tab = tab;
      wSub.interdiff = persistedInterdiff;
      wSub.checkedSize = checkedSize;
      wSub.topic = c.topic;
      wSub.chId = chId;
      wSub.subLen = subTogetherLen;
      wSub.cLen = changesLen;
      wSub.sLen = stacksLen;
      wSub.stackSig = stackSig;
      return htmlVal;
    };
    if (c._cachedRelationChainKey === relCacheKey && typeof c._cachedRelationChainHtml === "string") {
      return saveRelSub(c._cachedRelationChainHtml);
    }
    const html = AemuGerritReact.renderPgRelationChainHtml(c, getPgReactBridge());
    return saveRelSub(html);
  }

  function isPgStackCommitReviewed(clNum) {
    return state.reviewedStackCommits.has(Number(clNum));
  }

  function togglePgStackCommitReviewed(clNum, advanceToNext = false) {
    const num = Number(clNum);
    if (!num) return;
    const isNowReviewed = !state.reviewedStackCommits.has(num);
    if (isNowReviewed) {
      state.reviewedStackCommits.add(num);
      showToast(`ag/${num}: Marked commit as reviewed ✓`, 2000);
    } else {
      state.reviewedStackCommits.delete(num);
      showToast(`ag/${num}: Marked commit as unreviewed`, 2000);
    }
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (ls) {
        ls.setItem(REVIEWED_STACK_COMMITS_STORAGE_KEY, JSON.stringify(Array.from(state.reviewedStackCommits)));
      }
    } catch (e) {}

    const btn = document.getElementById(`btnToggleStackCommitReviewed-${num}`);
    if (btn) {
      if (btn.classList) btn.classList.toggle("is-reviewed", isNowReviewed);
      if (typeof btn.setAttribute === "function") {
        btn.setAttribute("title", isNowReviewed ? `ag/${num}: Reviewed (click to mark unreviewed)` : `Mark commit ag/${num} as reviewed (Shift+Enter)`);
      }
      const icon = typeof btn.querySelector === "function" ? btn.querySelector(".pg-btn-icon") : null;
      if (icon) icon.textContent = isNowReviewed ? "✓" : "○";
      const label = typeof btn.querySelector === "function" ? btn.querySelector(".pg-btn-label") : null;
      if (label) label.textContent = isNowReviewed ? "Commit Reviewed" : "Mark Reviewed";
    }

    syncPgStackUiState(state.selectedClNumber || num);

    if (advanceToNext && isNowReviewed) {
      goToNextStackCommit();
    }
  }

  function markAllStackCommitsReviewed(stackId) {
    const st = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!st || !Array.isArray(st.commits) || !st.commits.length) return;
    st.commits.forEach(c => state.reviewedStackCommits.add(Number(c.number)));
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (ls) {
        ls.setItem(REVIEWED_STACK_COMMITS_STORAGE_KEY, JSON.stringify(Array.from(state.reviewedStackCommits)));
      }
    } catch (e) {}
    showToast(`Reviewed all ${st.commits.length} commits in stack ✓`, 2500);
    renderDiffPane({ preserveScroll: true });
  }

  function resetStackCommitsReviewed(stackId) {
    const st = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!st || !Array.isArray(st.commits) || !st.commits.length) return;
    st.commits.forEach(c => state.reviewedStackCommits.delete(Number(c.number)));
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (ls) {
        ls.setItem(REVIEWED_STACK_COMMITS_STORAGE_KEY, JSON.stringify(Array.from(state.reviewedStackCommits)));
      }
    } catch (e) {}
    showToast(`Reset review status for all ${st.commits.length} commits in stack`, 2500);
    renderDiffPane({ preserveScroll: true });
  }

  function togglePgStackCommitCollapse(clNum) {
    const num = Number(clNum);
    if (!num) return;
    const isNowCollapsed = !state.collapsedStackCommits.has(num);
    if (isNowCollapsed) {
      state.collapsedStackCommits.add(num);
    } else {
      state.collapsedStackCommits.delete(num);
    }
    const sectionEl = document.getElementById(`pgStackCommit-${num}`);
    if (sectionEl) {
      sectionEl.classList.toggle("is-collapsed", isNowCollapsed);
    }
    const bodyEl = document.getElementById(`pgStackCommitBody-${num}`);
    if (bodyEl) {
      bodyEl.style.display = isNowCollapsed ? "none" : "";
    }
    const barEl = sectionEl ? sectionEl.querySelector(".pg-stack-commit-seq-bar") : null;
    if (barEl) {
      barEl.classList.toggle("is-collapsed", isNowCollapsed);
    }
    const toggleBtn = document.getElementById(`btnToggleStackCommitCollapse-${num}`);
    if (toggleBtn) {
      const icon = toggleBtn.querySelector(".pg-stack-collapse-icon");
      if (icon) icon.textContent = isNowCollapsed ? "▶" : "▼";
      toggleBtn.setAttribute("title", isNowCollapsed ? "Expand commit diff" : "Collapse commit diff");
    }
    const summaryEl = document.getElementById(`pgStackCommitSummary-${num}`);
    if (summaryEl) {
      summaryEl.style.display = isNowCollapsed ? "" : "none";
    }
  }

  function toggleAllStackCommitsCollapse() {
    const st = findPgStackForClOrId(state.selectedClNumber, state.selectedStackId);
    if (!st || !Array.isArray(st.commits) || !st.commits.length) return;
    const allCollapsed = st.commits.every(c => state.collapsedStackCommits.has(Number(c.number)));
    const shouldCollapse = !allCollapsed;
    st.commits.forEach(c => {
      const num = Number(c.number);
      if (shouldCollapse) {
        state.collapsedStackCommits.add(num);
      } else {
        state.collapsedStackCommits.delete(num);
      }
      const sectionEl = document.getElementById(`pgStackCommit-${num}`);
      if (sectionEl) sectionEl.classList.toggle("is-collapsed", shouldCollapse);
      const bodyEl = document.getElementById(`pgStackCommitBody-${num}`);
      if (bodyEl) bodyEl.style.display = shouldCollapse ? "none" : "";
      const barEl = sectionEl ? sectionEl.querySelector(".pg-stack-commit-seq-bar") : null;
      if (barEl) barEl.classList.toggle("is-collapsed", shouldCollapse);
      const toggleBtn = document.getElementById(`btnToggleStackCommitCollapse-${num}`);
      if (toggleBtn) {
        const icon = toggleBtn.querySelector(".pg-stack-collapse-icon");
        if (icon) icon.textContent = shouldCollapse ? "▶" : "▼";
        toggleBtn.setAttribute("title", shouldCollapse ? "Expand commit diff" : "Collapse commit diff");
      }
      const summaryEl = document.getElementById(`pgStackCommitSummary-${num}`);
      if (summaryEl) summaryEl.style.display = shouldCollapse ? "" : "none";
    });
    const btn = document.getElementById("btnStackToggleCollapseAll");
    if (btn) {
      btn.textContent = shouldCollapse ? "▼ Expand All" : "▲ Collapse All";
    }
    showToast(shouldCollapse ? "Collapsed all commits in stack" : "Expanded all commits in stack", 2000);
  }

  function goToNextStackCommit() {
    const st = findPgStackForClOrId(state.selectedClNumber, state.selectedStackId);
    if (!st || !Array.isArray(st.commits) || st.commits.length <= 1) return;
    const curNum = Number(state.selectedClNumber || (st.commits[0] && st.commits[0].number) || 0);
    const curIdx = st.commits.findIndex(c => Number(c.number) === curNum);
    if (curIdx >= 0 && curIdx < st.commits.length - 1) {
      const nextNum = Number(st.commits[curIdx + 1].number);
      if (state.continuousStackDiff) {
        scrollToStackCommit(nextNum);
      } else {
        selectStackAndCl(st.stack_id, nextNum, { animDir: "next" });
      }
      showToast(`Jumped to commit ${curIdx + 2}/${st.commits.length}: ag/${nextNum}`, 2000);
    } else {
      showToast("Already at tip commit in stack", 2000);
    }
  }

  function goToPrevStackCommit() {
    const st = findPgStackForClOrId(state.selectedClNumber, state.selectedStackId);
    if (!st || !Array.isArray(st.commits) || st.commits.length <= 1) return;
    const curNum = Number(state.selectedClNumber || (st.commits[0] && st.commits[0].number) || 0);
    const curIdx = st.commits.findIndex(c => Number(c.number) === curNum);
    if (curIdx > 0) {
      const prevNum = Number(st.commits[curIdx - 1].number);
      if (state.continuousStackDiff) {
        scrollToStackCommit(prevNum);
      } else {
        selectStackAndCl(st.stack_id, prevNum, { animDir: "prev" });
      }
      showToast(`Jumped to commit ${curIdx}/${st.commits.length}: ag/${prevNum}`, 2000);
    } else {
      showToast("Already at base commit in stack", 2000);
    }
  }

  function approveStackForBatchEdit(stackId) {
    const st = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!st || !Array.isArray(st.commits) || !st.commits.length) return;
    st.commits.forEach(c => state.checkedClNumbers.add(Number(c.number)));
    state.batchVoteState = state.batchVoteState || {};
    state.batchVoteState.code_review = 2;
    renderAll();
    if (typeof openBatchDialog === "function") {
      openBatchDialog();
    }
    showToast(`Selected all ${st.commits.length} commits in stack with Code-Review +2 staged`, 3000);
  }

  function getPgStackSubmitSequence(activeStack, commits) {
    if (!activeStack || !Array.isArray(commits) || commits.length === 0) {
      return {
        commits: [],
        items: [],
        total_commits: 0,
        reviewed_commits: 0,
        submittable_commits: 0,
        seq_ready_count: 0,
        all_ready: false,
        bottleneck_commit: null,
        bottleneck_reason: "",
        bottleneck_step: 0,
        _seqSig: "",
      };
    }

    let seqSig = "";
    for (let i = 0; i < commits.length; i++) {
      const c = commits[i];
      if (!c) continue;
      const num = Number(c.number);
      const isRev = isPgStackCommitReviewed(num) ? 1 : 0;
      const sub = isClSubmittable(c) ? 1 : 0;
      const crDone = Array.isArray(c.human_cr2) ? c.human_cr2.length : Number(c.human_cr2_count || 0);
      const crReq = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
      const revs = Array.isArray(c.reviewers_detail) ? c.reviewers_detail : [];
      const srLen = Array.isArray(c.submit_requirements) ? c.submit_requirements.length : 0;
      if (i > 0) seqSig += ";";
      seqSig += `${num}:${isRev}:${c.status || ""}:${sub}:${crDone}:${crReq}:${revs.length}:${revs[0] ? (revs[0].cr_vote || 0) : ""}:${c.presubmit_score || 0}:${c.presubmit_vetted ? 1 : 0}:${c.presubmit_status || ""}:${srLen}:${c.subject || ""}:${c.owner_name || c.owner_short || ""}:${c.branch || ""}:${c.unresolved_comment_count || 0}:${c.mergeable ?? ""}`;
    }
    if (activeStack._cachedSubmitSequenceKey === seqSig && activeStack._cachedSubmitSequence) {
      return activeStack._cachedSubmitSequence;
    }

    const total = commits.length;
    let reviewedCount = 0;
    let submittableCount = 0;
    let firstBlockedIdx = -1;
    let seqReadyCount = 0;
    let stillSeqReady = true;

    const items = commits.map((c, idx) => {
      const num = Number(c.number);
      const isReviewed = isPgStackCommitReviewed(num);
      if (isReviewed) reviewedCount++;

      const isMerged = String(c.status || "").toUpperCase() === "MERGED";
      const submittable = isMerged || isClSubmittable(c);
      if (submittable) submittableCount++;

      const blockerReason = submittable ? "" : describeClSubmitBlocker(c);

      let seqStatus = "ready";
      let blockingAncestor = null;
      let blockingStep = 0;

      if (isMerged) {
        seqStatus = "merged";
        if (stillSeqReady) seqReadyCount++;
      } else if (firstBlockedIdx === -1) {
        if (submittable) {
          seqStatus = "ready";
          if (stillSeqReady) seqReadyCount++;
        } else {
          seqStatus = "blocked_current";
          firstBlockedIdx = idx;
          stillSeqReady = false;
        }
      } else {
        seqStatus = "waiting_ancestor";
        blockingAncestor = commits[firstBlockedIdx];
        blockingStep = firstBlockedIdx + 1;
        stillSeqReady = false;
      }

      const crDone = Array.isArray(c.human_cr2) ? c.human_cr2.length : Number(c.human_cr2_count || 0);
      const crReq = Number(c.required_cr2_count || (c.is_agent_cl ? 2 : 1));
      const revs = Array.isArray(c.reviewers_detail) ? c.reviewers_detail : [];
      const minCr = revs.reduce((min, p) => Math.min(min, Number(p.cr_vote || 0)), 0);
      const crLabel = minCr < 0 ? `${minCr}` : (crDone >= crReq ? `+${crReq}` : `${crDone}/${crReq}`);

      const pv = getClPresubmitDisplay(c);
      const vScore = Number(c.presubmit_score || 0);
      const vLabel = (c.presubmit_vetted || vScore >= 1) ? "V +1" : (pv.status || "Pending");

      return {
        step: idx + 1,
        number: num,
        commit: c,
        subject: c.subject || `Commit ${idx + 1}`,
        author: c.owner_name || c.owner_short || (c.owner && (c.owner.name || c.owner.email)) || "Author",
        branch: getPgBranchDisplay(c.branch || ""),
        is_reviewed: isReviewed,
        is_merged: isMerged,
        is_submittable: submittable,
        blocker_reason: blockerReason,
        seq_status: seqStatus,
        blocking_ancestor: blockingAncestor,
        blocking_step: blockingStep,
        cr_label: crLabel,
        v_label: vLabel,
      };
    });

    const bottleneck = firstBlockedIdx >= 0 ? commits[firstBlockedIdx] : null;
    const bottleneckReason = firstBlockedIdx >= 0 ? describeClSubmitBlocker(bottleneck) : "";
    const allReady = (firstBlockedIdx === -1);

    const result = {
      commits,
      items,
      total_commits: total,
      reviewed_commits: reviewedCount,
      submittable_commits: submittableCount,
      seq_ready_count: seqReadyCount,
      all_ready: allReady,
      bottleneck_commit: bottleneck,
      bottleneck_reason: bottleneckReason,
      bottleneck_step: firstBlockedIdx >= 0 ? firstBlockedIdx + 1 : 0,
      _seqSig: seqSig,
    };
    activeStack._cachedSubmitSequenceKey = seqSig;
    activeStack._cachedSubmitSequence = result;
    return result;
  }

  function canPgStackInterdiff(commits) {
    if (!Array.isArray(commits) || commits.length === 0) return false;
    return commits.some(c => {
      const rawList = Array.isArray(c.patch_sets) && c.patch_sets.length > 0
        ? c.patch_sets
        : Array.from({ length: Math.max(1, Number(c.patch_set || 1)) }, (_, i) => i + 1);
      return rawList.length > 1;
    });
  }

  function isPgStackInterdiffActive(commits, stackId = null) {
    if (!Array.isArray(commits) || commits.length === 0) return false;
    const multiPsCommits = commits.filter(c => {
      const rawList = Array.isArray(c.patch_sets) && c.patch_sets.length > 0
        ? c.patch_sets
        : Array.from({ length: Math.max(1, Number(c.patch_set || 1)) }, (_, i) => i + 1);
      return rawList.length > 1;
    });
    if (multiPsCommits.length === 0) return false;
    return multiPsCommits.every(c => {
      const num = Number(c.number);
      const sel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || (typeof getPgSelectedPatchset === "function" ? getPgSelectedPatchset(num) : null) || {};
      const bundle = state.diffsByCl && state.diffsByCl[num];
      const base = String(sel.base || (bundle && bundle.base_patch_set) || "base");
      return base !== "base" && base !== "0";
    });
  }

  async function togglePgStackInterdiff(stackId = null) {
    const activeStack = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    const targetStackId = (activeStack && activeStack.stack_id) || stackId || state.selectedStackId;
    const commits = (activeStack && Array.isArray(activeStack.commits)) ? activeStack.commits : [];
    if (!commits || commits.length === 0) {
      if (typeof showToast === "function") showToast("No commits found in current stack", 2000);
      return;
    }
    const multiPsCommits = commits.filter(c => {
      const rawList = Array.isArray(c.patch_sets) && c.patch_sets.length > 0
        ? c.patch_sets
        : Array.from({ length: Math.max(1, Number(c.patch_set || 1)) }, (_, i) => i + 1);
      return rawList.length > 1;
    });
    if (multiPsCommits.length === 0) {
      if (typeof showToast === "function") showToast("No commits in stack have multiple patchsets for interdiff", 2500);
      return;
    }
    const isCurrentlyActive = isPgStackInterdiffActive(commits, targetStackId);
    if (isCurrentlyActive) {
      if (typeof setPgPersistedStackInterdiff === "function") {
        setPgPersistedStackInterdiff(targetStackId, false);
      }
      for (const c of commits) {
        await resetPgPatchsetComparisonToBase(c.number, false);
      }
      if (typeof showToast === "function") showToast("✕ Stack diff reset: comparing all commits vs Base (Alt+I)", 2500);
    } else {
      if (typeof setPgPersistedStackInterdiff === "function") {
        setPgPersistedStackInterdiff(targetStackId, true);
      }
      for (const c of multiPsCommits) {
        await togglePgDiffPreviousPatchset(c.number, false);
      }
      if (typeof showToast === "function") showToast(`⚡ Stack Interdiff ON: comparing PS n-1 → PS n across ${multiPsCommits.length} commits (Alt+I)`, 3000);
    }
    renderDiffPane();
    if (typeof syncPgRelationSlots === "function") {
      syncPgRelationSlots();
    }
  }

  function renderPgContinuousStackBannerHtml(activeStack, commits) {
    if (!activeStack || !Array.isArray(commits) || commits.length <= 1) return "";
    const stackId = activeStack.stack_id || "";
    const canInterdiff = canPgStackInterdiff(commits);
    let isInterdiff = isPgStackInterdiffActive(commits, stackId);
    if (canInterdiff && !isInterdiff && getPgPersistedStackInterdiff(stackId)) {
      ensurePgStackInterdiffRestored(activeStack);
      isInterdiff = true;
    }
    const seq = getPgStackSubmitSequence(activeStack, commits);
    let filesAndCollapseSig = "";
    for (let i = 0; i < commits.length; i++) {
      const c = commits[i];
      if (!c) continue;
      const num = Number(c.number);
      const b = state.diffsByCl && state.diffsByCl[num];
      const fLen = b && Array.isArray(b.files) ? b.files.length : (Array.isArray(c.files) ? c.files.length : 0);
      const isCol = state.collapsedStackCommits && state.collapsedStackCommits.has(num) ? 1 : 0;
      if (i > 0) filesAndCollapseSig += ",";
      filesAndCollapseSig += `${num}:${fLen}:${c.insertions || 0}:${c.deletions || 0}:${isCol}`;
    }
    const bannerKey = `${stackId}|${activeStack.title || ""}|${state.selectedClNumber || 0}|${canInterdiff ? 1 : 0}|${isInterdiff ? 1 : 0}|${filesAndCollapseSig}|${seq._seqSig || ""}`;
    if (activeStack._cachedContinuousBannerKey === bannerKey && typeof activeStack._cachedContinuousBannerHtml === "string") {
      return activeStack._cachedContinuousBannerHtml;
    }
    const html = AemuGerritReact.renderPgContinuousStackBannerHtml(activeStack, commits, getPgReactBridge());
    activeStack._cachedContinuousBannerKey = bannerKey;
    activeStack._cachedContinuousBannerHtml = html;
    return html;
  }

  function renderPgStackCommitSequenceBarHtml(c, idx, totalCount, activeStack) {
    if (!c) return "";
    const num = Number(c.number);
    const commits = (activeStack && Array.isArray(activeStack.commits)) ? activeStack.commits : [];
    const seq = getPgStackSubmitSequence(activeStack, commits);
    const isCol = Boolean(state.collapsedStackCommits && state.collapsedStackCommits.has(num));
    const isRev = isPgStackCommitReviewed(num);
    const b = state.diffsByCl && state.diffsByCl[num];
    const bFilesLen = b && Array.isArray(b.files) ? b.files.length : -1;
    const cFilesLen = Array.isArray(c.files) ? c.files.length : -1;
    const prevNum = (idx > 0 && commits[idx - 1]) ? Number(commits[idx - 1].number) : 0;
    const nextNum = (idx < totalCount - 1 && commits[idx + 1]) ? Number(commits[idx + 1].number) : 0;
    const seqBarKey = `${num}|${idx}|${totalCount}|${c.subject || ""}|${isCol ? 1 : 0}|${isRev ? 1 : 0}|${bFilesLen}:${cFilesLen}|${prevNum}|${nextNum}|${seq._seqSig || ""}`;
    if (c._cachedStackSeqBarKey === seqBarKey && typeof c._cachedStackSeqBarHtml === "string") {
      return c._cachedStackSeqBarHtml;
    }
    const html = AemuGerritReact.renderPgStackCommitSequenceBarHtml(c, idx, totalCount, activeStack, getPgReactBridge());
    c._cachedStackSeqBarKey = seqBarKey;
    c._cachedStackSeqBarHtml = html;
    return html;
  }

  function scrollToStackCommit(clNum) {
    const num = Number(clNum);
    if (!num) return;
    state.selectedClNumber = num;
    if (state.collapsedStackCommits && state.collapsedStackCommits.has(num)) {
      togglePgStackCommitCollapse(num);
    }
    const el = document.getElementById(`pgStackCommit-${num}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      el.classList.add("pg-stack-focused-flash");
      setTimeout(() => el.classList.remove("pg-stack-focused-flash"), 1300);
    }
    tryFastSelectClInQueueDom(num);
    syncPgStackUiState(num);
  }

  function scrollDiffToTop() {
    const diffEl = document.getElementById("diffScroll");
    if (diffEl) {
      diffEl.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  function selectStackForBatchEdit(stackId) {
    const st = findPgStackForClOrId(state.selectedClNumber, stackId);
    if (!st || !Array.isArray(st.commits)) return;
    st.commits.forEach(c => state.checkedClNumbers.add(Number(c.number)));
    renderAll();
    showToast(`Checked all ${st.commits.length} commits in stack for Batch Edit`, 2500);
  }

  function toggleContinuousStackDiff(event, stackId) {
    if (event && typeof event.stopPropagation === "function") event.stopPropagation();
    const st = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!st || !Array.isArray(st.commits) || st.commits.length <= 1) {
      showToast("This change is not part of a multi-commit stack", 2500);
      return;
    }
    state.continuousStackDiff = !state.continuousStackDiff;
    if (state.continuousStackDiff && state.codeViewerMode) {
      state.codeViewerMode = false;
    }
    try {
      const ls = (typeof window !== "undefined" && window.localStorage)
        ? window.localStorage
        : (typeof localStorage !== "undefined" ? localStorage : null);
      if (ls) {
        ls.setItem(CONTINUOUS_STACK_DIFF_STORAGE_KEY, state.continuousStackDiff ? "true" : "false");
      }
    } catch (err) {}

    if (state.continuousStackDiff) {
      prefetchDiffsForNumbers(st.commits.map(c => Number(c.number)), {
        trackLoading: true,
        label: `Loading diffs for entire stack (${st.commits.length} CLs)…`,
      });
      showToast(`📚 Continuous Stack Review: ON (${st.commits.length} commits in relation chain)`, 3000);
    } else {
      showToast("📄 Continuous Stack Review: OFF (Single CL view)", 2500);
    }
    renderDiffPane();
    if (state.continuousStackDiff && state.selectedClNumber) {
      scrollToStackCommit(state.selectedClNumber);
    }
    syncPgRelationSlots();
  }

  function syncPgStackUiState(activeNum) {
    const activeStack = findPgStackForClOrId(activeNum, state.selectedStackId);
    const commits = (activeStack && Array.isArray(activeStack.commits)) ? activeStack.commits : [];
    const totalCommits = commits.length;
    const currentNum = Number(activeNum || state.selectedClNumber || (commits[0] && commits[0].number) || 0);
    const currentIdx = commits.findIndex(c => Number(c.number) === currentNum);

    document.querySelectorAll(".pg-stack-step-pill").forEach(p => {
      const clNum = Number(p.getAttribute("data-cl") || 0);
      const isTarget = clNum === currentNum;
      p.classList.toggle("active", isTarget);
      p.classList.toggle("is-reviewed", isPgStackCommitReviewed(clNum));
    });

    document.querySelectorAll(".pg-relation-item").forEach(item => {
      const title = item.getAttribute("title") || "";
      item.classList.toggle("is-current", title.startsWith(`ag/${currentNum}:`));
    });

    const prevBtn = document.getElementById("btnStackPrevCommit");
    if (prevBtn) prevBtn.disabled = (currentIdx <= 0);
    const nextBtn = document.getElementById("btnStackNextCommit");
    if (nextBtn) nextBtn.disabled = (currentIdx < 0 || currentIdx >= totalCommits - 1);

    if (totalCommits > 0) {
      const reviewedCount = commits.filter(c => isPgStackCommitReviewed(c.number)).length;
      const pct = Math.round((reviewedCount / totalCommits) * 100);
      const progressText = document.getElementById("pgStackProgressText");
      if (progressText) {
        progressText.textContent = `${reviewedCount} of ${totalCommits} commits reviewed (${pct}%)`;
      }
      const progressFill = document.getElementById("pgStackProgressFill");
      if (progressFill) {
        progressFill.style.width = `${pct}%`;
      }
      const completeBadge = document.getElementById("pgStackCompleteBadge");
      if (completeBadge) {
        completeBadge.style.display = (reviewedCount === totalCommits) ? "inline-flex" : "none";
      }
      const markAllBtn = document.getElementById("btnStackMarkAllReviewed");
      if (markAllBtn) {
        markAllBtn.textContent = (reviewedCount === totalCommits) ? "Reset Reviews" : "Mark All Reviewed ✓";
      }
      const filesDrawerBtn = document.getElementById("btnStackFilesDrawer");
      if (filesDrawerBtn) {
        const stackFilesSummary = getPgCumulativeStackFiles(activeStack, commits);
        filesDrawerBtn.innerHTML = `<span class="pg-btn-icon">📁</span> Stack Files (${stackFilesSummary.total_files})`;
      }
      const seq = getPgStackSubmitSequence(activeStack, commits);
      const submitSeqBtn = document.getElementById("btnStackSubmitSeq");
      if (submitSeqBtn) {
        submitSeqBtn.className = `btn pg-btn-stack-action pg-btn-stack-seq ${seq.all_ready ? "is-ready" : ""}`;
        submitSeqBtn.innerHTML = `<span class="pg-btn-icon">${seq.all_ready ? "✓" : "📋"}</span> Submit Sequence (${seq.seq_ready_count}/${totalCommits} Ready)`;
        submitSeqBtn.title = "Inspect stack submit sequence & checkpoints (Alt+T)";
      }
      const seqBadge = document.getElementById("pgStackSequenceStatusBadge");
      if (seqBadge) {
        seqBadge.className = `pg-stack-seq-status-badge ${seq.all_ready ? "ready" : "pending"}`;
        seqBadge.textContent = seq.all_ready ? "⚡ Stack Ready" : `⏳ Step ${seq.bottleneck_step}/${totalCommits} Next`;
        seqBadge.title = seq.all_ready ? "Entire stack is submittable in sequence! (Alt+T)" : `Bottleneck at Step ${seq.bottleneck_step}: ag/${seq.bottleneck_commit ? seq.bottleneck_commit.number : ""} (${seq.bottleneck_reason}) (Alt+T)`;
      }
      if (isPgStackSequenceDialogOpen()) {
        renderPgStackSequenceDialogBodyDom(activeStack.stack_id);
      }
      if (isPgStackFilesDrawerOpen()) {
        renderPgStackFilesDialogBodyDom(activeStack.stack_id);
      }
    }
  }

  function isPgStackSequenceDialogOpen() {
    return Boolean(state.stackSequenceDialogOpen);
  }

  function renderPgStackSequenceDialogBodyDom(stackId) {
    const activeStack = findPgStackForClOrId(state.selectedClNumber, stackId || state.selectedStackId);
    if (!activeStack || !Array.isArray(activeStack.commits) || activeStack.commits.length <= 1) {
      closePgStackSequenceDialog();
      return;
    }
    const commits = activeStack.commits;
    const seq = getPgStackSubmitSequence(activeStack, commits);

    const titleEl = document.getElementById("pgStackSeqTitle");
    if (titleEl) titleEl.textContent = "Stack Submit Sequence & Checkpoints";

    const subEl = document.getElementById("pgStackSeqSubtitle");
    if (subEl) {
      subEl.textContent = `${activeStack.title || activeStack.stack_id || "Relation Stack"} · ${seq.total_commits} commits in topological submission sequence`;
    }

    const heroEl = document.getElementById("pgStackSeqHeroBanner");
    if (heroEl) {
      heroEl.className = `pg-stack-seq-hero ${seq.all_ready ? "is-all-ready" : "is-blocked"}`;
      heroEl.innerHTML = AemuGerritReact.renderPgStackSequenceHeroHtml(
        seq,
        activeStack.stack_id || ""
      );
    }

    const cpEl = document.getElementById("pgStackSeqCheckpointsBar");
    if (cpEl) {
      cpEl.innerHTML = AemuGerritReact.renderPgStackSequenceCheckpointsHtml(seq);
    }

    const tableContainer = document.getElementById("pgStackSeqTableContainer");
    if (tableContainer) {
      tableContainer.innerHTML = AemuGerritReact.renderPgStackSequenceTableHtml(
        seq,
        Number(state.selectedClNumber),
        activeStack.stack_id || ""
      );
    }

    const footerStats = document.getElementById("pgStackSeqFooterStats");
    if (footerStats) {
      footerStats.textContent = `${seq.reviewed_commits} of ${seq.total_commits} reviewed · ${seq.seq_ready_count} of ${seq.total_commits} ready in sequence`;
    }
  }

