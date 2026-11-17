  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F") && !e.shiftKey && !e.altKey) {
      const activeCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      const changeView = document.querySelector(".pg-change-view");
      if (activeCl && changeView) {
        e.preventDefault();
        openPgDiffFindBar(activeCl);
        return;
      }
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === "g" || e.key === "G") && !e.shiftKey && !e.altKey) {
      const activeCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      const changeView = document.querySelector(".pg-change-view");
      if (activeCl && changeView) {
        e.preventDefault();
        openPgGoToLineModal(activeCl);
        return;
      }
    }
    if (e.key === "F3" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const activeCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      const changeView = document.querySelector(".pg-change-view");
      if (activeCl && changeView) {
        e.preventDefault();
        if (!state.diffFind || !state.diffFind.isOpen) {
          openPgDiffFindBar(activeCl);
        } else {
          stepPgDiffFindMatch(e.shiftKey ? "prev" : "next", activeCl);
        }
        return;
      }
    }
    if (state.rebaseDialogOpen && ((e.ctrlKey || e.metaKey) && e.key === "Enter")) {
      e.preventDefault();
      executePgRebase();
      return;
    }
    if (state.revertDialogOpen && ((e.ctrlKey || e.metaKey) && e.key === "Enter")) {
      e.preventDefault();
      executePgRevert();
      return;
    }
    if (state.cherryPickDialogOpen && ((e.ctrlKey || e.metaKey) && e.key === "Enter")) {
      e.preventDefault();
      executePgCherryPick();
      return;
    }
    if (state.confirmDiscardDialogOpen && ((e.ctrlKey || e.metaKey) && e.key === "Enter")) {
      e.preventDefault();
      executePgConfirmDiscard();
      return;
    }
    if (e.key === "Escape" && state.confirmDiscardDialogOpen) {
      e.preventDefault();
      closePgConfirmDiscardDialog();
      return;
    }
    if (e.key === "Escape") {
      const bugCard = document.getElementById("pgBugHovercard");
      if (bugCard && bugCard.style && bugCard.style.display === "block") {
        e.preventDefault();
        hidePgBugHovercard();
        return;
      }
      const ownerTt = document.getElementById("pgOwnerTooltip");
      if (ownerTt && ownerTt.classList && ownerTt.classList.contains("is-visible")) {
        e.preventDefault();
        hidePgOwnerTooltip();
        return;
      }
    }
    if (e.key === "Escape" && state.codeOwnersDialogOpen) {
      e.preventDefault();
      closePgCodeOwnersDialog();
      return;
    }
    if (e.key === "Escape" && state.addAttentionPopoverOpen) {
      e.preventDefault();
      closeAllPgAddAttentionPopovers();
      hidePgAttentionHovercard();
      return;
    }
    if (e.key === "Escape" && state.diffFind && state.diffFind.isOpen) {
      e.preventDefault();
      closePgDiffFindBar(state.diffFind.cl || state.selectedClNumber);
      return;
    }
    if (e.key === "Escape" && state.goToLineModalOpen) {
      e.preventDefault();
      closePgGoToLineModal();
      return;
    }
    if (e.key === "Escape" && state.fileJump && state.fileJump.isOpen) {
      e.preventDefault();
      closePgFileJumpMenu(state.fileJump.cl || state.selectedClNumber);
      return;
    }
    if (state.downloadDialogOpen) {
      if (e.key === "Escape") {
        e.preventDefault();
        closePgDownloadDialog();
        return;
      }
      const isBranchInput = e.target && e.target.id === "pgDownloadBranchInput";
      if (isBranchInput && e.key === "Enter") {
        e.preventDefault();
        copyPgDownloadCommand();
        return;
      }
      const isReadonlyOrNotInput = !e.target || e.target.tagName !== "INPUT" || e.target.readOnly;
      if (isReadonlyOrNotInput) {
        if (e.key === "1") {
          e.preventDefault();
          selectPgDownloadTab("repo");
          return;
        }
        if (e.key === "2") {
          e.preventDefault();
          selectPgDownloadTab("checkout");
          return;
        }
        if (e.key === "3") {
          e.preventDefault();
          selectPgDownloadTab("cherry-pick");
          return;
        }
        if (e.key === "4") {
          e.preventDefault();
          selectPgDownloadTab("format-patch");
          return;
        }
        if (e.key === "5") {
          e.preventDefault();
          selectPgDownloadTab("pull");
          return;
        }
        if (e.key === "6") {
          e.preventDefault();
          selectPgDownloadTab("patch");
          return;
        }
        if (e.key === "7") {
          e.preventDefault();
          selectPgDownloadTab("all");
          return;
        }
        if (e.key === "s" || e.key === "S") {
          e.preventDefault();
          cyclePgDownloadScheme();
          return;
        }
        if (e.key === "[" || (e.altKey && (e.key === "p" || e.key === "P"))) {
          e.preventDefault();
          stepPgDownloadPatchset(-1);
          return;
        }
        if (e.key === "]") {
          e.preventDefault();
          stepPgDownloadPatchset(1);
          return;
        }
        if ((e.key === "c" || e.key === "C" || e.key === "Enter") && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          copyPgDownloadCommand();
          return;
        }
      }
    }
    if (e.key === "Escape" && state.rebaseDialogOpen) {
      e.preventDefault();
      closePgRebaseDialog();
      return;
    }
    if (e.key === "Escape" && state.abandonDialogOpen) {
      e.preventDefault();
      closePgAbandonDialog();
      return;
    }
    if (e.key === "Escape" && state.restoreDialogOpen) {
      e.preventDefault();
      closePgRestoreDialog();
      return;
    }
    if (e.key === "Escape" && state.revertDialogOpen) {
      e.preventDefault();
      closePgRevertDialog();
      return;
    }
    if (e.key === "Escape" && state.setWipDialogOpen) {
      e.preventDefault();
      closePgSetWipDialog();
      return;
    }
    if (e.key === "Escape" && state.setReadyDialogOpen) {
      e.preventDefault();
      closePgSetReadyDialog();
      return;
    }
    if (e.key === "Escape" && state.fileEditorOpen) {
      e.preventDefault();
      closePgFileEditorDialog();
      return;
    }
    if (e.key === "Escape" && state.cherryPickDialogOpen) {
      e.preventDefault();
      closePgCherryPickDialog();
      return;
    }
    if (e.key === "Escape" && state.followUpDialogOpen) {
      e.preventDefault();
      closePgFollowUpDialog();
      return;
    }
    const checksRerunBackdrop = document.getElementById("pgChecksRerunBackdrop");
    if (e.key === "Escape" && checksRerunBackdrop && checksRerunBackdrop.classList.contains("is-open")) {
      e.preventDefault();
      closePgChecksRerunModal();
      return;
    }
    if (e.key === "Escape" && state.savedSearchesModalOpen) {
      e.preventDefault();
      closeSavedSearchesModal();
      return;
    }
    if (e.key === "Escape" && state.saveSearchModalOpen) {
      e.preventDefault();
      closeSaveCurrentSearchDialog();
      return;
    }
    if (e.key === "Escape" && state.copyLinksMenu && state.copyLinksMenu.isOpen) {
      e.preventDefault();
      closePgCopyLinksMenu();
      return;
    }
    if (e.key === "Escape" && state.copyFilePathsMenuCl) {
      e.preventDefault();
      closePgCopyFilePathsMenu();
      return;
    }
    if (e.key === "Escape" && state.activeFileOutlineMenu) {
      e.preventDefault();
      closeAllPgFileOutlineMenus();
      return;
    }
    if (e.key === "Escape" && state.srAuditDialogOpen) {
      e.preventDefault();
      const searchInp = document.getElementById("pgSrAuditSearchInput");
      if (searchInp && searchInp.value) {
        clearPgSrAuditFilter();
      } else {
        closePgSubmitRequirementsAuditDialog();
      }
      return;
    }
    if (e.key === "Escape" && state.attentionHistoryDialogOpen) {
      e.preventDefault();
      const searchInp = document.getElementById("pgAttnHistSearchInput");
      if (searchInp && searchInp.value) {
        clearPgAttentionHistoryFilter();
      } else {
        closePgAttentionHistoryDialog();
      }
      return;
    }
    if (e.key === "Escape" && state.includedInDialogOpen) {
      e.preventDefault();
      const filterInp = document.getElementById("pgIncludedInFilterInput");
      if (filterInp && filterInp.value) {
        clearPgIncludedInFilter();
      } else {
        closePgIncludedInDialog();
      }
      return;
    }
    if (e.key === "Escape" && state.patchRangeMenuCl) {
      e.preventDefault();
      closePgPatchRangeMenu();
      return;
    }
    if (e.key === "Escape" && state.diffPreferencesDialogOpen) {
      e.preventDefault();
      closePgDiffPreferencesDialog();
      return;
    }
    if (e.key === "Escape" && state.stackSequenceDialogOpen) {
      e.preventDefault();
      closePgStackSequenceDialog();
      return;
    }
    if (e.key === "Escape" && state.stackFilesDialogOpen) {
      e.preventDefault();
      closePgStackFilesDrawer();
      return;
    }
    if (e.key === "Escape" && state.shortcutsDialogOpen) {
      e.preventDefault();
      const inp = document.getElementById("pgShortcutsSearchInput");
      if (inp && inp.value) {
        clearShortcutsSearch();
      } else {
        closeShortcutsDialog();
      }
      return;
    }
    if (e.key === "Escape" && state.updateWarningModalOpen && !state.sidecarUpdateInFlight) {
      e.preventDefault();
      closeUpdateDirtyWarningModal();
      return;
    }
    if (e.key === "Escape" && state.harnessMenuOpen) {
      closeHarnessMenu();
      return;
    }
    if (e.key === "Escape" && state.replyDialogOpen && !state.votingInFlight) {
      e.preventDefault();
      cancelReplyDialog();
      return;
    }
    if (e.key === "Escape" && state.batchReviewerComposer && !state.batchReviewerComposer.submitting) {
      closeBatchReviewerComposer();
      return;
    }
    if (e.key === "Escape" && state.activeChatComposer && !state.activeChatComposer.submitting) {
      closeChatDmComposer();
      return;
    }
    if (e.key === "Escape" && state.activeReviewerComposer) {
      closeReviewerComposer();
      return;
    }
    if (e.key === "Escape" && state.gutterDrag && state.gutterDrag.active) {
      e.preventDefault();
      cancelPgGutterDrag();
      return;
    }
    if (e.key === "Escape" && state.dashboardDrag && state.dashboardDrag.active) {
      e.preventDefault();
      cancelPgDashboardDrag();
      return;
    }
    if (e.key === "Escape" && state.codeSelection) {
      e.preventDefault();
      closeCodeSelectionComposer(e, { discardDraft: true });
      return;
    }
    if (e.altKey && (e.key === "m" || e.key === "M" || e.code === "KeyM") && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      if (state.codeSelection) {
        e.preventDefault();
        const nextMode = (state.codeSelection.mode === "comment") ? "ask" : "comment";
        setCodeSelectionMode(nextMode, { fromKeyboard: true });
        return;
      }
      const curCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (curCl) {
        e.preventDefault();
        const curFile = (state.activeFileByCl && state.activeFileByCl[curCl]) || "";
        const c = (state.changes || []).find(ch => Number(ch.number) === curCl);
        const bundle = state.diffsByCl && state.diffsByCl[curCl];
        const isMerge = (typeof isPgMergeCommit === "function") && isPgMergeCommit(c, bundle);
        if (curFile === "/COMMIT_MSG" && isMerge) {
          jumpToPgMergeList(curCl);
        } else {
          jumpToPgCommitMessage(curCl);
        }
        return;
      }
    }
    if (state.codeSelection && e.altKey && (e.key === "1" || e.code === "Digit1")) {
      e.preventDefault();
      setCodeSelectionMode("comment", { fromKeyboard: true });
      return;
    }
    if (state.codeSelection && e.altKey && (e.key === "2" || e.code === "Digit2")) {
      e.preventDefault();
      setCodeSelectionMode("ask", { fromKeyboard: true });
      return;
    }
    if (state.searchHelpDialogOpen) {
      if (e.key === "Escape") {
        e.preventDefault();
        closePgSearchHelpDialog();
        return;
      }
    }
    if (e.altKey && (e.key === "/" || e.key === "?")) {
      e.preventDefault();
      togglePgSearchHelpDialog();
      return;
    }
    if (e.key === "Escape" && closeOpenInlineReplyComposers()) {
      return;
    }
    if (e.key === "Escape" && state.checkedClNumbers && state.checkedClNumbers.size > 0) {
      e.preventDefault();
      clearCheckedCls();
      if (typeof showToast === "function") {
        showToast("Cleared batch selection (Esc)");
      }
      return;
    }
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleShortcutsDialog();
      return;
    }
    if (e.key === "o" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      const changeView = document.querySelector(".pg-change-view");
      if (changeView && state.selectedClNumber) {
        openFocusedPgFileDiff(state.selectedClNumber);
      } else {
        openSelectedCl();
      }
      return;
    }
    if (e.key === "I" && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        const c = (state.changes || []).find(ch => ch.number === state.selectedClNumber);
        const ps = (c && c.patch_set) || 1;
        expandAllPgFileDiffs(state.selectedClNumber, ps);
        return;
      }
    }
    if ((e.key === "F" || (e.key === "f" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        const input = document.getElementById(`pgFileFilterInput-${state.selectedClNumber}`) || document.querySelector(".pg-file-filter-input");
        if (input) {
          input.focus();
          input.select();
        }
        return;
      }
    }
    if (e.key === "f" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgActiveFileReviewed();
        return;
      }
    }
    if ((e.key === "V" || (e.key === "v" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgMarkAllReviewed(state.selectedClNumber);
        return;
      }
    }
    if (e.key === "i" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgActiveFileDiffCollapse();
        return;
      }
    }
    if (e.key === "d" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        openPgDownloadDialog(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "C" || (e.key === "c" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        openPgCherryPickDialog(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "U" || (e.key === "u" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        openPgFollowUpDialog(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "G" || (e.key === "g" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        openPgIncludedInDialog(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "L" || (e.key === "l" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgCopyLinksMenu(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "Q" || (e.key === "q" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgAddAttentionPopover(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "B" || (e.key === "b" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgAttentionHistoryDialog(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "Z" || (e.key === "z" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgSubmitRequirementsAuditDialog(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "S" || (e.key === "s" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleContinuousStackDiff();
      return;
    }
    if ((e.key === "{" || (e.key === "[" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const activeStack = typeof findPgStackForClOrId === "function" ? findPgStackForClOrId(state.selectedClNumber, state.selectedStackId) : null;
      if (state.continuousStackDiff || (activeStack && Array.isArray(activeStack.commits) && activeStack.commits.length > 1)) {
        e.preventDefault();
        goToPrevStackCommit();
        return;
      }
    }
    if ((e.key === "}" || (e.key === "]" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const activeStack = typeof findPgStackForClOrId === "function" ? findPgStackForClOrId(state.selectedClNumber, state.selectedStackId) : null;
      if (state.continuousStackDiff || (activeStack && Array.isArray(activeStack.commits) && activeStack.commits.length > 1)) {
        e.preventDefault();
        goToNextStackCommit();
        return;
      }
    }
    if (e.key === "Enter" && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.continuousStackDiff && state.selectedClNumber) {
        e.preventDefault();
        togglePgStackCommitReviewed(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "X" || (e.key === "x" && e.shiftKey)) && e.shiftKey && e.altKey && !e.ctrlKey && !e.metaKey) {
      const curCl = Number(state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
      if (curCl) {
        e.preventDefault();
        const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
        const activeFile = activeBlock ? activeBlock.getAttribute("data-file") : ((state.activeFileByCl && state.activeFileByCl[curCl]) || (state.focusedFileByCl && state.focusedFileByCl[curCl]) || null);
        if (activeFile) {
          togglePgFileContext(null, curCl, activeFile);
        }
        return;
      }
    }
    if ((e.key === "X" || (e.key === "x" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      expandAllVisibleDiffContext();
      return;
    }
    if ((e.key === "O" || (e.key === "o" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgOwnersFilter(state.selectedClNumber);
        return;
      }
    }
    if (((e.key === "o" || e.key === "O") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) ||
        ((e.key === "o" || e.key === "O") && (e.ctrlKey || e.metaKey) && e.shiftKey && !e.altKey)) {
      const curCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (curCl) {
        e.preventDefault();
        const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
        const curFile = activeBlock ? activeBlock.getAttribute("data-file") : ((state.activeFileByCl && state.activeFileByCl[curCl]) || (state.focusedFileByCl && state.focusedFileByCl[curCl]) || null);
        if (curFile) {
          togglePgFileOutlineMenu(curCl, curFile);
        }
        return;
      }
    }
    if ((e.key === "C" || (e.key === "c" && e.shiftKey)) && e.shiftKey && e.altKey && !e.ctrlKey && !e.metaKey) {
      const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (num) {
        e.preventDefault();
        cyclePgCommentsAuthorFilter(num);
        return;
      }
    }
    if ((e.key === "H" || (e.key === "h" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (num) {
        e.preventDefault();
        cyclePgCommentsFilter(num);
        return;
      }
    }
    if ((e.key === "h" || e.key === "H") && e.altKey && !e.ctrlKey && !e.metaKey) {
      const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (num) {
        e.preventDefault();
        showPgAddHashtagInput(num);
        if (typeof showToast === "function") {
          showToast("Add hashtag to change (Alt+H)");
        }
        return;
      }
    }
    if ((e.key === "r" || e.key === "R") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (num) {
        e.preventDefault();
        togglePgRelatedChangesTab(num);
        return;
      }
    }
    if ((e.key === "u" || e.key === "U") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (num) {
        e.preventDefault();
        openPgRebaseDialog(num);
        return;
      }
    }
    if ((e.key === "w" || e.key === "W") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (num) {
        e.preventDefault();
        togglePgWorkInProgress(num);
        return;
      }
    }
    if ((e.key === "J" || (e.key === "j" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgFileJumpMenu(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "T" || (e.key === "t" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        cyclePgFileSort(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "Y" || (e.key === "y" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleSavedSearchesModal();
      return;
    }
    if ((e.key === "y" || e.key === "Y") && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const activeEl = document.activeElement;
      const isDiffFocus = Boolean(
        (state.activeLine && state.activeLine.lineNum) ||
        (state.codeSelection && state.codeSelection.active) ||
        (activeEl && (activeEl.closest ? activeEl.closest(".code-table, .diff-pane, #diffScroll, .pg-file-diff-block, .code-line") : null))
      );
      if (isDiffFocus && (state.activeLine || state.codeSelection)) {
        e.preventDefault();
        copyPgDiffActiveLinePermalink();
        return;
      }
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgFileTreeMode(state.selectedClNumber);
        return;
      }
    }
    if ((e.key === "]" || e.code === "BracketRight") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      navigatePgUnreviewedFileDiff("next");
      return;
    }
    if ((e.key === "[" || e.code === "BracketLeft") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      navigatePgUnreviewedFileDiff("prev");
      return;
    }
    if ((e.key === "u" || e.key === "U") && e.shiftKey && e.altKey && !e.ctrlKey && !e.metaKey) {
      const curCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (curCl) {
        e.preventDefault();
        jumpToFirstUnreviewedFile(curCl);
        return;
      }
    }
    if ((e.key === "j" || e.key === "J") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      const curCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (curCl) {
        e.preventDefault();
        navigatePgFileListCursor(curCl, "next-unreviewed");
        return;
      }
    }
    if ((e.key === "k" || e.key === "K") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      const curCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (curCl) {
        e.preventDefault();
        navigatePgFileListCursor(curCl, "prev-unreviewed");
        return;
      }
    }
    if (e.key === "]" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      navigatePgFileDiff("next");
      return;
    }
    if (e.key === "u" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      const changeView = document.querySelector(".pg-change-view");
      const diffContainer = document.getElementById("diffPane") || document.getElementById("diffScroll") || window;
      const scrollPos = diffContainer.scrollTop !== undefined ? diffContainer.scrollTop : (window.scrollY || 0);
      if (changeView && scrollPos > 120 && state.selectedClNumber) {
        scrollToPgFileList(state.selectedClNumber);
      } else {
        toggleSidebarCollapsed();
      }
      return;
    }
    if (e.key === "[" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      navigatePgFileDiff("prev");
      return;
    }
    if ((e.key === "n" || e.key === "N") && e.shiftKey && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      navigatePgDiffChunk("next", { chunksOnly: true });
      return;
    }
    if ((e.key === "p" || e.key === "P") && e.shiftKey && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      navigatePgDiffChunk("prev", { chunksOnly: true });
      return;
    }
    if (e.key === "n" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.diffFind && state.diffFind.isOpen && state.diffFind.matches && state.diffFind.matches.length > 0) {
        e.preventDefault();
        stepPgDiffFindMatch("next", state.diffFind.cl);
        return;
      }
      e.preventDefault();
      navigatePgDiffChunk("next");
      return;
    }
    if (e.key === "p" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.diffFind && state.diffFind.isOpen && state.diffFind.matches && state.diffFind.matches.length > 0) {
        e.preventDefault();
        stepPgDiffFindMatch("prev", state.diffFind.cl);
        return;
      }
      e.preventDefault();
      navigatePgDiffChunk("prev");
      return;
    }
    if ((e.key === "N" || (e.key === "n" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.diffFind && state.diffFind.isOpen && state.diffFind.matches && state.diffFind.matches.length > 0) {
        e.preventDefault();
        stepPgDiffFindMatch("prev", state.diffFind.cl);
        return;
      }
      e.preventDefault();
      navigatePgUnresolvedThread("next");
      return;
    }
    if ((e.key === "P" || (e.key === "p" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const changeView = document.querySelector(".pg-change-view");
      const hasUnres = changeView && changeView.querySelector(".diff-inline-comment:not(.resolved):not(.agent-question):not(.draft-item), .swarm-finding[data-thread-ui-id]");
      if (hasUnres) {
        e.preventDefault();
        navigatePgUnresolvedThread("prev");
        return;
      }
      toggleStagedPr();
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
      return;
    }
    if ((e.key === "R" || (e.key === "r" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgShowResolvedComments();
      return;
    }
    if ((e.key === "W" || (e.key === "w" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgShowRobotComments();
      return;
    }
    if ((e.key === "E" || (e.key === "e" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const selectedCl = state.selectedClNumber;
      if (selectedCl) {
        const commentsTabPane = document.getElementById(`pgCommentsTabPane-${selectedCl}`);
        const isCommentsTabActive = commentsTabPane && commentsTabPane.style.display !== "none";
        if (isCommentsTabActive) {
          e.preventDefault();
          togglePgAllCommentsCollapse(selectedCl);
          return;
        }
        e.preventDefault();
        const c = (state.changes || []).find(ch => ch.number === selectedCl);
        const ps = (c && c.patch_set) || 1;
        collapseAllPgFileDiffs(selectedCl, ps);
        return;
      }
    }
    if ((e.key === "D" || (e.key === "d" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      navigatePgDraftComment("next");
      return;
    }
    if ((e.key === "M" || (e.key === "m" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const activeImgViewer = document.querySelector(".pg-image-viewer:hover, .pg-image-viewer:focus-within") || (state.activeDiffFile && typeof isPgImageFile === "function" && isPgImageFile(state.activeDiffFile) ? document.querySelector(".pg-image-viewer") : null);
      if (activeImgViewer) {
        e.preventDefault();
        cyclePgImageDiffMode();
        return;
      }
      e.preventDefault();
      markActiveFileReviewedAndGoToNextUnreviewed();
      return;
    }
    if (e.key === " " && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const activeImgViewer = document.querySelector(".pg-image-viewer:hover, .pg-image-viewer:focus-within") || document.querySelector(".pg-image-viewer");
      if (activeImgViewer) {
        e.preventDefault();
        const cl = Number(activeImgViewer.getAttribute("data-cl") || state.selectedClNumber);
        const fileId = activeImgViewer.id.replace(`pgImageViewer-${cl}-`, "");
        flipPgImageSides(cl, fileId);
        return;
      }
    }
    if ((e.key === "c" || e.key === "C") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      cycleDiffColorScheme();
      return;
    }
    if ((e.key === "d" || e.key === "D") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (e.shiftKey) {
        stepPgDiffLineHeight(-1);
      } else {
        stepPgDiffLineHeight(+1);
      }
      return;
    }
    if ((e.key === "=" || e.key === "+") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      stepPgDiffFontSize(+1);
      return;
    }
    if ((e.key === "-" || e.key === "_") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      stepPgDiffFontSize(-1);
      return;
    }
    if (e.key === "0" && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (e.shiftKey) {
        setPgDiffLineHeight(1.5, true, false);
        if (typeof showToast === "function") {
          showToast("Diff line spacing reset to default 1.50 (Shift+Alt+0)");
        }
      } else {
        setPgDiffFontSize(12, true, false);
      }
      return;
    }
    if ((e.key === "g" || e.key === "G") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      cyclePgDiffLineLength();
      return;
    }
    if ((e.key === "ArrowUp" || e.key === "Up") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      stepPgDiffContext(1);
      return;
    }
    if ((e.key === "ArrowDown" || e.key === "Down") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      stepPgDiffContext(-1);
      return;
    }
    if ((e.key === "x" || e.key === "X") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      cyclePgDiffContext();
      return;
    }
    if ((e.key === "s" || e.key === "S") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      cyclePgFileStatusFilter(state.selectedClNumber);
      return;
    }
    if ((e.key === "e" || e.key === "E") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      cyclePgFileExtensionFilter(state.selectedClNumber);
      return;
    }
    if ((e.key === "b" || e.key === "B") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (typeof isPgDiffNavContext === "function" && isPgDiffNavContext()) {
        const focusedBlock = (typeof document !== "undefined")
          ? (document.querySelector(".pg-file-diff-block:hover, .pg-file-diff-block:focus-within") ||
             document.querySelector(".pg-file-diff-block"))
          : null;
        const targetFile = focusedBlock ? focusedBlock.getAttribute("data-file") : (state.activeLine && state.activeLine.filePath);
        if (targetFile) {
          togglePgFileBlame(e, state.selectedClNumber, targetFile);
          return;
        }
      }
      cyclePgFileSizeFilter(state.selectedClNumber);
      return;
    }
    if ((e.key === "y" || e.key === "Y") && e.altKey && !e.ctrlKey && !e.metaKey) {
      const selectedCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (selectedCl) {
        e.preventDefault();
        const isTree = getPgFileTreeMode(selectedCl);
        if (!isTree) {
          setPgFileTreeMode(selectedCl, true);
        } else {
          toggleAllPgDirsCollapse(selectedCl);
        }
        return;
      }
    }
    if ((e.key === "t" || e.key === "T") && e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      togglePgDiffShowTabs();
      return;
    }
    if ((e.key === "w" || e.key === "W") && e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      togglePgDiffShowWhitespaceErrors();
      return;
    }
    if ((e.key === "i" || e.key === "I") && e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      togglePgDiffIntraline();
      return;
    }
    if ((e.key === "t" || e.key === "T") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (typeof isPgStackSequenceDialogOpen === "function" && isPgStackSequenceDialogOpen()) {
        togglePgStackSequenceDialog();
        return;
      }
      const num = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (num && typeof showPgEditTopicInput === "function") {
        showPgEditTopicInput(num);
        if (typeof showToast === "function") {
          showToast("Add or edit topic on change (Alt+T)");
        }
        return;
      }
      togglePgStackSequenceDialog();
      return;
    }
    if ((e.key === "f" || e.key === "F") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      togglePgStackFilesDrawer();
      return;
    }
    if ((e.key === "n" || e.key === "N") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      navigatePgCommentThread("next");
      return;
    }
    if ((e.key === "p" || e.key === "P") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      navigatePgCommentThread("prev");
      return;
    }
    if (e.key === "c" && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const selectedCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (selectedCl) {
        e.preventDefault();
        triggerPgAddComment(selectedCl);
        return;
      }
    }
    if (e.key === "q" && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const curNum = Number(state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
      if (curNum) {
        const hoveredMsgCard = document.querySelector(".pg-message-card:hover, .pg-message-card:focus-within");
        if (hoveredMsgCard) {
          const idx = Number(hoveredMsgCard.getAttribute("data-msg-idx") || 0);
          e.preventDefault();
          quotePgMessage(e, curNum, idx);
          return;
        }
        const hoveredThread = document.querySelector(".diff-inline-comment:not(.draft-item):hover, .diff-inline-comment:not(.draft-item):focus-within");
        if (hoveredThread) {
          const itemId = hoveredThread.getAttribute("data-inline-id");
          if (itemId) {
            e.preventDefault();
            quoteInlineCommentInReply(itemId, curNum);
            return;
          }
        }
        if (typeof window !== "undefined" && window.getSelection) {
          const sel = window.getSelection();
          if (sel && !sel.isCollapsed && sel.toString().trim()) {
            const anchor = sel.anchorNode;
            const el = anchor && (anchor.nodeType === 1 ? anchor : anchor.parentElement);
            const anchorCard = el && el.closest ? el.closest(".pg-message-card, .diff-inline-comment") : null;
            if (anchorCard && anchorCard.classList.contains("pg-message-card")) {
              const idx = Number(anchorCard.getAttribute("data-msg-idx") || 0);
              e.preventDefault();
              quotePgMessage(e, curNum, idx);
              return;
            }
            if (anchorCard && anchorCard.classList.contains("diff-inline-comment")) {
              const itemId = anchorCard.getAttribute("data-inline-id");
              if (itemId) {
                e.preventDefault();
                quoteInlineCommentInReply(itemId, curNum);
                return;
              }
            }
          }
        }
      }
    }
    if (e.key === "\\" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgCodeViewerMode();
      return;
    }
    if ((e.key === "v" || e.key === "V") && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgDiffViewMode();
      return;
    }
    if ((e.key === "w" || e.key === "W") && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgDiffWordWrap();
      return;
    }
    if ((e.key === "a" || e.key === "A") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      togglePgLeftDiffPane();
      return;
    }
    if ((e.key === "A" || (e.key === "a" && e.shiftKey)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const isDiffFocus = e.target && (e.target.closest ? e.target.closest(".code-table, .diff-pane, #diffScroll, .pg-file-diff-block, .code-line") : null);
      if (isDiffFocus) {
        e.preventDefault();
        togglePgLeftDiffPane();
        return;
      }
    }
    if ((e.key === "o" || e.key === "O") && e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      togglePgDiffOverviewBar();
      return;
    }
    if ((e.key === "o" || e.key === "O") && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      const isDiffFocus = e.target && (e.target.closest ? e.target.closest(".code-table, .diff-pane, #diffScroll, .pg-file-diff-block, .code-line") : null);
      if (isDiffFocus) {
        e.preventDefault();
        togglePgDiffOverviewBar();
        return;
      }
    }
    if (e.key === "," && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgDiffPreferencesDialog();
      return;
    }
    if ((e.key === "i" || e.key === "I") && e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      togglePgStackInterdiff();
      return;
    }
    if ((e.key === "b" || e.key === "B") && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const hoveredImgViewer = document.querySelector(".pg-image-viewer:hover, .pg-image-viewer:focus-within");
      if (hoveredImgViewer) {
        e.preventDefault();
        const cl = Number(hoveredImgViewer.getAttribute("data-cl") || state.selectedClNumber);
        const fileId = hoveredImgViewer.id.replace(`pgImageViewer-${cl}-`, "");
        cyclePgImageBackground(cl, fileId);
        return;
      }
      if (state.continuousStackDiff) {
        e.preventDefault();
        togglePgStackInterdiff();
        return;
      }
      if (state.selectedClNumber) {
        e.preventDefault();
        const sel = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[state.selectedClNumber]) || {};
        const bundle = state.diffsByCl && state.diffsByCl[state.selectedClNumber];
        const currentBase = String(sel.base || (bundle && bundle.base_patch_set) || "base");
        if (currentBase !== "base" && currentBase !== "0") {
          resetPgPatchsetComparisonToBase(state.selectedClNumber);
        } else {
          togglePgDiffPreviousPatchset(state.selectedClNumber);
        }
        return;
      }
    }
    if (e.key === "z" && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const activeEl = document.activeElement;
      const inChangeLog = Boolean(activeEl && activeEl.closest && activeEl.closest(".pg-change-log-section"));
      const selectedCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (inChangeLog && selectedCl) {
        e.preventDefault();
        collapseAllPgMessages(selectedCl);
        return;
      }
      const hoveredImageViewer = document.querySelector(".pg-image-viewer:hover, .pg-image-viewer:focus-within");
      if (hoveredImageViewer) {
        e.preventDefault();
        const cl = Number(hoveredImageViewer.getAttribute("data-cl") || state.selectedClNumber);
        const fileId = hoveredImageViewer.id.replace(`pgImageViewer-${cl}-`, "");
        cyclePgImageZoom(cl, fileId, false);
        return;
      }
      if (selectedCl) {
        e.preventDefault();
        togglePgCommitMessage(selectedCl);
        return;
      }
    }
    if ((e.key === "z" || e.key === "Z") && e.altKey && !e.ctrlKey && !e.metaKey) {
      const viewer = document.querySelector(".pg-image-viewer:hover, .pg-image-viewer:focus-within") || document.querySelector(".pg-image-viewer");
      if (viewer) {
        e.preventDefault();
        const cl = Number(viewer.getAttribute("data-cl") || state.selectedClNumber);
        const fileId = viewer.id.replace(`pgImageViewer-${cl}-`, "");
        cyclePgImageZoom(cl, fileId, e.shiftKey);
        return;
      }
    }
    if (e.key === "e" && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const selectedCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (selectedCl) {
        const hoveredOrFocusedComment = document.querySelector(".diff-inline-comment:hover, .diff-inline-comment:focus, .diff-inline-comment:focus-within");
        if (hoveredOrFocusedComment) {
          const itemId = hoveredOrFocusedComment.getAttribute("data-inline-id");
          if (itemId) {
            e.preventDefault();
            if (hoveredOrFocusedComment.classList.contains("draft-item") && !hoveredOrFocusedComment.classList.contains("is-editing")) {
              editInlineDraftInPlace(selectedCl, itemId);
            } else {
              toggleInlineItemCollapse(selectedCl, itemId);
              const isCollapsed = hoveredOrFocusedComment.classList.contains("collapsed");
              if (typeof showToast === "function") {
                showToast(isCollapsed ? "Collapsed comment thread (e)" : "Expanded comment thread (e)");
              }
            }
            return;
          }
        }
        e.preventDefault();
        const activeBlock = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
        const activeFile = activeBlock ? activeBlock.getAttribute("data-file") : ((state.activeFileByCl && state.activeFileByCl[selectedCl]) || null);
        if (activeFile === "/COMMIT_MSG") {
          openPgCommitMsgEditor(selectedCl);
        } else {
          openPgFileEditor(selectedCl, activeFile);
        }
        return;
      }
    }
    if (e.key === "h" && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const selectedCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (selectedCl) {
        e.preventDefault();
        togglePgHideReviewed(selectedCl);
        return;
      }
    }
    if (e.key === "ArrowUp" && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (isPgDiffNavContext()) {
        e.preventDefault();
        stepPgDiffLineSelection(-1, state.selectedClNumber);
        return;
      }
      if (state.selectedClNumber) {
        e.preventDefault();
        stepPgPatchset(state.selectedClNumber, -1);
        return;
      }
    }
    if (e.key === "ArrowDown" && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (isPgDiffNavContext()) {
        e.preventDefault();
        stepPgDiffLineSelection(1, state.selectedClNumber);
        return;
      }
      if (state.selectedClNumber) {
        e.preventDefault();
        stepPgPatchset(state.selectedClNumber, 1);
        return;
      }
    }
    if ((e.key === "K" || (e.key === "k" && e.shiftKey)) && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgChangeTab();
      return;
    }
    if (e.key === "t" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      togglePgTheme();
      return;
    }
    if ((e.key === "l" || e.key === "L") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      const activeTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[state.selectedClNumber]) || "files";
      if (activeTab === "checks" && state.selectedClNumber) {
        e.preventDefault();
        togglePgCheckLogDrawer(state.selectedClNumber);
        return;
      }
      e.preventDefault();
      togglePgDiffLineNumbers();
      return;
    }
    if (e.key === "m" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        scrollToChangeLog(state.selectedClNumber);
        return;
      }
    }
    if (e.key === "*" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (state.selectedClNumber) {
        e.preventDefault();
        togglePgStarChange(state.selectedClNumber);
        return;
      }
    }
    if (e.key === "/") {
      if (state.shortcutsDialogOpen) {
        e.preventDefault();
        const scInp = document.getElementById("pgShortcutsSearchInput");
        if (scInp) {
          scInp.focus();
          scInp.select();
        }
        return;
      }
      if (state.sidebarCollapsed) {
        toggleSidebarCollapsed(false);
      }
      const inp = document.getElementById("queueSearchInput");
      if (inp) {
        e.preventDefault();
        inp.focus();
        inp.select();
        openPgSearchSuggestions();
      }
      return;
    }
    if (e.key === ":") {
      const activeCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      const changeView = document.querySelector(".pg-change-view");
      if (activeCl && changeView) {
        e.preventDefault();
        openPgGoToLineModal(activeCl);
        return;
      }
    }
    if (e.key === "Escape" && state.searchSuggestionsOpen) {
      closePgSearchSuggestions();
      return;
    }
    if (e.key === "Escape" && state.checkedClNumbers.size > 0) {
      clearCheckedCls();
      return;
    }
    if (e.key === "Escape" && state.codeViewerMode && !state.codeSelection) {
      e.preventDefault();
      const cvNum = getActiveCodeViewerClNumber();
      const cvFilter = (cvNum && state.codeViewerFilterByCl && state.codeViewerFilterByCl[cvNum]) || "";
      if (cvFilter) {
        clearCodeViewerFilter();
      } else {
        togglePgCodeViewerMode(null, false);
      }
      return;
    }
    if (e.key === "Escape" && hasActiveAuthorOrSearchFilter()) {
      clearQueueSearchAndAuthor();
      return;
    }
    if (isPgDiffNavContext()) {
      if (e.key === "Escape") {
        if (state.codeSelection) {
          e.preventDefault();
          clearCodeSelection({ discardDraft: true });
          if (typeof showToast === "function") {
            showToast("Cancelled line selection (Esc)");
          }
          return;
        }
      }
      if (e.key === "j" || e.key === "J" || e.key === "ArrowDown") {
        e.preventDefault();
        if (e.shiftKey) {
          stepPgDiffLineSelection(1, state.selectedClNumber);
        } else {
          navigatePgDiffLine(1, state.selectedClNumber);
        }
        return;
      }
      if (e.key === "k" || e.key === "K" || e.key === "ArrowUp") {
        e.preventDefault();
        if (e.shiftKey) {
          stepPgDiffLineSelection(-1, state.selectedClNumber);
        } else {
          navigatePgDiffLine(-1, state.selectedClNumber);
        }
        return;
      }
      if (e.key === "ArrowLeft" || ((e.key === "a" || e.key === "A") && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey)) {
        e.preventDefault();
        switchPgDiffSide("left", state.selectedClNumber);
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        switchPgDiffSide("right", state.selectedClNumber);
        return;
      }
      if ((e.key === "b" || e.key === "B") && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const hasActiveSbsLine = state.activeLine && state.diffMode === "side-by-side";
        if (hasActiveSbsLine && state.activeLine.isBase) {
          switchPgDiffSide("right", state.selectedClNumber);
        } else {
          const focusedBlock = (typeof document !== "undefined")
            ? (document.querySelector(".pg-file-diff-block:hover, .pg-file-diff-block:focus-within") ||
               document.querySelector(".pg-file-diff-block"))
            : null;
          const targetFile = focusedBlock ? focusedBlock.getAttribute("data-file") : (state.activeLine && state.activeLine.filePath);
          if (targetFile) {
            togglePgFileBlame(e, state.selectedClNumber, targetFile);
          }
        }
        return;
      }
    }
    const isSidebarTarget = Boolean(
      document.activeElement &&
      typeof document.activeElement.closest === "function" &&
      document.activeElement.closest(".queue-panel, #queueList, .cl-card, .pg-dashboard-row, .queue-batch-strip")
    );
    const activeTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[state.selectedClNumber]) || "files";
    const changeView = document.querySelector(".pg-change-view");
    const isFileListTarget = Boolean(
      (document.activeElement &&
       typeof document.activeElement.closest === "function" &&
       document.activeElement.closest(".pg-file-list-table, .pg-files-container, .pg-tree-rows, .pg-file-row, .pg-dir-row, .pg-magic-file-row")) ||
      (changeView && state.selectedClNumber && !isSidebarTarget && activeTab === "files")
    );
    if (isFileListTarget && state.selectedClNumber) {
      if (e.key === "Escape") {
        clearPgFileListRangeSelection(true);
        return;
      }
      if ((e.key === "u" || e.key === "U") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
        if (state.fileListRangeSelection && Array.isArray(state.fileListRangeSelection.files) && state.fileListRangeSelection.files.length > 0) {
          e.preventDefault();
          executePgRangeAction(state.selectedClNumber, "unreviewed");
          return;
        }
      }
      if (e.key === "j" || e.key === "J" || e.key === "ArrowDown") {
        e.preventDefault();
        const prevFocused = (state.focusedFileByCl && state.focusedFileByCl[state.selectedClNumber]) || "";
        navigatePgFileListCursor(state.selectedClNumber, "next");
        const focusedFile = state.focusedFileByCl && state.focusedFileByCl[state.selectedClNumber];
        if (focusedFile) {
          focusPgFileRow(state.selectedClNumber, focusedFile, { focusElement: true });
          if (e.shiftKey) {
            const anchor = (state.fileListRangeSelection && state.fileListRangeSelection.anchor) || prevFocused;
            updatePgFileListRangeSelection(state.selectedClNumber, anchor, focusedFile);
          } else {
            clearPgFileListRangeSelection();
          }
        }
        return;
      }
      if (e.key === "k" || e.key === "K" || e.key === "ArrowUp") {
        e.preventDefault();
        const prevFocused = (state.focusedFileByCl && state.focusedFileByCl[state.selectedClNumber]) || "";
        navigatePgFileListCursor(state.selectedClNumber, "prev");
        const focusedFile = state.focusedFileByCl && state.focusedFileByCl[state.selectedClNumber];
        if (focusedFile) {
          focusPgFileRow(state.selectedClNumber, focusedFile, { focusElement: true });
          if (e.shiftKey) {
            const anchor = (state.fileListRangeSelection && state.fileListRangeSelection.anchor) || prevFocused;
            updatePgFileListRangeSelection(state.selectedClNumber, anchor, focusedFile);
          } else {
            clearPgFileListRangeSelection();
          }
        }
        return;
      }
      if (e.key === "x" || e.key === "X" || e.key === "v" || e.key === "V" || e.key === "r" || e.key === "R") {
        e.preventDefault();
        if (state.fileListRangeSelection && Array.isArray(state.fileListRangeSelection.files) && state.fileListRangeSelection.files.length > 0) {
          const num = state.selectedClNumber;
          const rangeFiles = state.fileListRangeSelection.files;
          const revSet = getPgReviewedSet(num);
          const allRev = rangeFiles.every(f => revSet.has(f));
          const nextRev = !allRev;
          setPgFilesReviewedRange(num, null, rangeFiles, nextRev);
          clearPgFileListRangeSelection();
          if (typeof showToast === "function") {
            showToast(
              nextRev
                ? `Marked ${rangeFiles.length} files as reviewed ✓ (${e.key})`
                : `Marked ${rangeFiles.length} files as unreviewed (${e.key})`
            );
          }
          return;
        }
        togglePgFocusedFileReviewed(state.selectedClNumber, { keyLabel: e.key.toLowerCase() });
        return;
      }
    }
    const orderedItems = getOrderedVisibleCommits(getVisibleStacks());
    const flatVisible = orderedItems.map(item => item.commit);
    if (!flatVisible.length) return;
    if (e.key === "j") {
      if (activeTab === "checks" && state.selectedClNumber) {
        e.preventDefault();
        navigatePgCheckRun(state.selectedClNumber, "next");
        return;
      }
      navigateVisibleCl("next");
    } else if (e.key === "k") {
      if (activeTab === "checks" && state.selectedClNumber) {
        e.preventDefault();
        navigatePgCheckRun(state.selectedClNumber, "prev");
        return;
      }
      navigateVisibleCl("prev");
    } else if (e.key === "x") {
      const activeEl = document.activeElement;
      const inChangeLog = Boolean(activeEl && activeEl.closest && activeEl.closest(".pg-change-log-section"));
      const selectedCl = state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number);
      if (inChangeLog && selectedCl) {
        e.preventDefault();
        expandAllPgMessages(selectedCl);
        return;
      }
      if (state.selectedClNumber) {
        toggleClCheckbox(state.selectedClNumber);
      }
    } else if (e.key === "r" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const curNum = Number(state.selectedClNumber || (state.changes[0] && state.changes[0].number) || 0);
      const activeTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[curNum]) || "files";
      if (activeTab === "checks") {
        e.preventDefault();
        openPgChecksRerunModal(curNum);
      } else if (state.checkedClNumbers.size === 0) {
        const hoveredThread = document.querySelector(".diff-inline-comment:not(.draft-item):hover, .diff-inline-comment:not(.draft-item):focus-within");
        if (hoveredThread) {
          const itemId = hoveredThread.getAttribute("data-inline-id");
          if (itemId && typeof openInlineReplyComposer === "function") {
            e.preventDefault();
            openInlineReplyComposer(curNum, itemId);
            return;
          }
        }
        e.preventDefault();
        openReplyDialog({ focusComment: true });
      }
    } else if (e.key === "0") {
      toggleStagedCr(0);
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
    } else if (e.key === "1") {
      toggleStagedCr(1);
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
    } else if (e.key === "a" || e.key === "2") {
      toggleStagedCr(2);
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
    } else if (e.key === "A") {
      ensureStagedVoteContextSynced();
      state.stagedVote.cr = 2;
      state.stagedVote.as = 1;
      syncActiveActionButtons(getActiveCommitsForDiff(), false);
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
    } else if (e.key === "u") {
      toggleStagedAs();
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
    } else if (e.key === "p") {
      toggleStagedPr();
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
    } else if (e.altKey && (e.key === "v" || e.key === "V")) {
      e.preventDefault();
      toggleStagedVerified(1);
      if (state.checkedClNumbers.size === 0) openReplyDialog({ focusComment: false });
    } else if (e.altKey && (e.key === "l" || e.key === "L")) {
      e.preventDefault();
      applyPgReplyQuickPreset("lgtm_cr2");
      if (!state.replyDialogOpen && state.checkedClNumbers.size === 0) {
        openReplyDialog({ focusComment: true });
      }
    } else if (e.key === "Enter" && computeStagedVotePayload().hasAnyChange) {
      e.preventDefault();
      submitStagedVotes();
    } else if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const isFileListTarget = Boolean(
        document.activeElement &&
        document.activeElement.closest(".pg-file-list-table, .pg-files-container, .pg-tree-rows, .pg-file-row, .pg-magic-file-row")
      );
      if (isFileListTarget && state.selectedClNumber) {
        e.preventDefault();
        openFocusedPgFileDiff(state.selectedClNumber);
        return;
      }
      e.preventDefault();
      openSelectedCl();
    } else if (e.key === "s") {
      triggerHarness("review-swarm");
    }
  });

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("click", () => {
      if (state.harnessMenuOpen) {
        closeHarnessMenu();
      }
      if (state.batchReviewerComposer && !state.batchReviewerComposer.submitting) {
        closeBatchReviewerComposer();
      }
      if (state.activeReviewerComposer && !state.activeReviewerComposer.submitting) {
        closeReviewerComposer();
      }
    });
  }

  const PG_SCROLL_HOVERCARD_COOLDOWN_MS = 140;
  let _pgDiffScrollRafId = 0;

  function recordPgScrollActivity() {
    state._lastScrollActivityTs = Date.now();
    if (typeof hidePgAttentionHovercard === "function") hidePgAttentionHovercard();
    if (typeof hidePgBugHovercard === "function") hidePgBugHovercard();
    if (typeof hidePgOwnerTooltip === "function") hidePgOwnerTooltip();
  }

  function isPgActivelyScrolling(event) {
    if (!event || (event.type !== "mouseenter" && event.type !== "mouseover")) {
      return false;
    }
    const lastTs = Number(state._lastScrollActivityTs || 0);
    return lastTs > 0 && (Date.now() - lastTs) < PG_SCROLL_HOVERCARD_COOLDOWN_MS;
  }

  function isPgSidebarScrollTarget(target) {
    if (!target || typeof target !== "object") return false;
    const id = String(target.id || "");
    if (id === "queueList" || id === "queuePane" || id === "fileRailList" || id === "cvTreeScroll" || id === "codeViewerSidebar") {
      return true;
    }
    if (id === "diffScroll" || id === "diffPane") {
      return false;
    }
    if (typeof target.closest === "function") {
      if (target.closest("#diffScroll, #diffPane")) return false;
      if (target.closest("#queuePane, #queueList, #fileRailList, #cvTreeScroll, #codeViewerSidebar, .pg-dropdown-menu, .pg-modal-body")) {
        return true;
      }
    }
    return false;
  }

  function runPgDiffScrollSyncNow() {
    _pgDiffScrollRafId = 0;
    syncPgActiveFileFromScroll();
    if (typeof syncPgDiffOverviewThumbs === "function") {
      syncPgDiffOverviewThumbs();
    }
  }

  function schedulePgDiffScrollSync() {
    if (typeof requestAnimationFrame === "function") {
      if (_pgDiffScrollRafId) return;
      _pgDiffScrollRafId = requestAnimationFrame(runPgDiffScrollSyncNow);
      return;
    }
    runPgDiffScrollSyncNow();
  }

  function handlePgWindowScroll(event) {
    recordPgScrollActivity();
    if (state.harnessMenuOpen) {
      positionHarnessDropdownMenu();
    }
    const target = event && event.target ? event.target : null;
    if (isPgSidebarScrollTarget(target)) {
      return;
    }
    schedulePgDiffScrollSync();
  }

  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("resize", () => {
      syncSidebarLayoutDom();
      syncPgStickyFileHeaders();
      if (state.harnessMenuOpen) {
        positionHarnessDropdownMenu();
      }
    });

    window.addEventListener("wheel", recordPgScrollActivity, { capture: true, passive: true });
    window.addEventListener("scroll", handlePgWindowScroll, { capture: true, passive: true });

    window.addEventListener("click", (e) => {
      if (state.fileJump && state.fileJump.isOpen) {
        const wrap = document.getElementById(`pgFileJumpGroup-${state.fileJump.cl}`);
        if (wrap && !wrap.contains(e.target)) {
          closePgFileJumpMenu(state.fileJump.cl);
        }
      }
      if (state.copyLinksMenu && state.copyLinksMenu.isOpen) {
        const wrap = document.getElementById(`pgCopyLinksWrap-${state.copyLinksMenu.cl}`);
        if (wrap && !wrap.contains(e.target)) {
          closePgCopyLinksMenu();
        }
      }
      if (state.copyFilePathsMenuCl) {
        const wrap = document.getElementById(`pgCopyFilePathsWrap-${state.copyFilePathsMenuCl}`);
        if (wrap && !wrap.contains(e.target)) {
          closePgCopyFilePathsMenu();
        }
      }
      if (state.activeFileOutlineMenu) {
        const safeFileId = escapeHtml(String(state.activeFileOutlineMenu.file || "").replace(/[^a-zA-Z0-9_-]/g, "_"));
        const wrap = document.getElementById(`pgFileOutlineWrap-${state.activeFileOutlineMenu.cl}-${safeFileId}`);
        if (wrap && !wrap.contains(e.target)) {
          closeAllPgFileOutlineMenus();
        }
      }
      if (state.addAttentionPopoverOpen) {
        const isInside = e.target && e.target.closest && (e.target.closest(".pg-add-attention-popover") || e.target.closest(".pg-btn-add-attention"));
        if (!isInside) {
          closeAllPgAddAttentionPopovers();
        }
      }
      if (state.patchRangeMenuCl) {
        const isInsideMenu = e.target && e.target.closest && (e.target.closest(".pg-patch-range-popover") || e.target.closest(".pg-btn-patch-range-toggle"));
        if (!isInsideMenu) {
          closePgPatchRangeMenu();
        }
      }
      const hovercard = document.getElementById("pgAttentionHovercard");
      if (hovercard && hovercard.style.display === "block") {
        const isInsideCard = e.target && e.target.closest && (e.target.closest("#pgAttentionHovercard") || e.target.closest(".pg-attention-chip"));
        if (!isInsideCard) {
          hidePgAttentionHovercard();
        }
      }
      const bugHovercard = document.getElementById("pgBugHovercard");
      if (bugHovercard && bugHovercard.style && bugHovercard.style.display === "block") {
        const isInsideBugCard = e.target && e.target.closest && (e.target.closest("#pgBugHovercard") || e.target.closest("[data-bug-id]"));
        if (!isInsideBugCard) {
          hidePgBugHovercard();
        }
      }
      const ownerTt = document.getElementById("pgOwnerTooltip");
      if (ownerTt && ownerTt.style && ownerTt.style.display === "block") {
        const isInsideTt = e.target && e.target.closest && (e.target.closest("#pgOwnerTooltip") || e.target.closest(".pg-owner-badge"));
        if (!isInsideTt) {
          hidePgOwnerTooltip();
        }
      }
      if (state.searchSuggestionsOpen) {
        const isInsideSearch = e.target && e.target.closest && e.target.closest(".pg-header-search-wrap");
        if (!isInsideSearch) {
          closePgSearchSuggestions();
        }
      }
    });

    function onPgWindowHashChange() {
      if (typeof checkPgMessageHashAnchor === "function") checkPgMessageHashAnchor();
      if (typeof checkPgDiffLineHashAnchor === "function") checkPgDiffLineHashAnchor();
    }
    window.addEventListener("hashchange", onPgWindowHashChange);
    setTimeout(onPgWindowHashChange, 100);

    window.addEventListener("keyup", (e) => {
      if (e.key === "Shift" && typeof clearPgFileListRangeHover === "function") {
        clearPgFileListRangeHover();
      }
    });
  }

  /* ================= Commit Code Viewer Mode Controller ================= */
  const CODE_VIEWER_COMMIT_MSG_PATH = "/COMMIT_MSG";

  function getActiveCodeViewerClNumber(explicitClNum = null) {
    const num = Number(
      explicitClNum ||
      state.selectedClNumber ||
      (state.changes && state.changes[0] && state.changes[0].number) ||
      0
    );
    return num > 0 ? num : 0;
  }

  function getCodeViewerPatchSet(clNum, bundle = null, c = null) {
    const num = Number(clNum);
    const selPs = (num && typeof getPgSelectedPatchset === "function")
      ? getPgSelectedPatchset(num)
      : null;
    return Number((selPs && selPs.ps) || (bundle && bundle.patch_set) || (c && c.patch_set) || 1);
  }

  function getCodeViewerCollapsedSet(clNum) {
    const num = Number(clNum);
    state.codeViewerCollapsedDirsByCl = state.codeViewerCollapsedDirsByCl || {};
    if (!state.codeViewerCollapsedDirsByCl[num]) {
      state.codeViewerCollapsedDirsByCl[num] = new Set();
    }
    return state.codeViewerCollapsedDirsByCl[num];
  }

  function resolveCodeViewerSelectedFile(clNum, bundle) {
    const num = Number(clNum);
    const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : _PG_EMPTY_ARR;
    state.codeViewerSelectedFileByCl = state.codeViewerSelectedFileByCl || {};
    const existing = state.codeViewerSelectedFileByCl[num] || "";
    if (existing === CODE_VIEWER_COMMIT_MSG_PATH) {
      return existing;
    }
    if (existing && rawFiles.some(f => f && f.file === existing)) {
      return existing;
    }
    const activeFile = (state.activeFileByCl && state.activeFileByCl[num]) || "";
    if (activeFile && rawFiles.some(f => f && f.file === activeFile)) {
      state.codeViewerSelectedFileByCl[num] = activeFile;
      return activeFile;
    }
    const c = findPgChangeByNumber(num);
    const sorted = (rawFiles.length > 0 && typeof getPgSortedFileList === "function")
      ? getPgSortedFileList(c || { number: num }, rawFiles)
      : rawFiles;
    const fallback = (sorted.length > 0 && sorted[0] && sorted[0].file)
      ? sorted[0].file
      : CODE_VIEWER_COMMIT_MSG_PATH;
    state.codeViewerSelectedFileByCl[num] = fallback;
    return fallback;
  }

  function buildCodeViewerFileEntries(c, bundle, curPs) {
    const num = Number(c && c.number);
    const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : _PG_EMPTY_ARR;
    if (rawFiles.length === 0) return [];
    const revSet = getPgReviewedSet(num, curPs);
    const conflictSet = new Set([
      ...((c && Array.isArray(c.conflicts)) ? c.conflicts : _PG_EMPTY_ARR),
      ...((bundle && Array.isArray(bundle.conflicts)) ? bundle.conflicts : _PG_EMPTY_ARR),
    ]);
    const out = new Array(rawFiles.length);
    for (let i = 0; i < rawFiles.length; i++) {
      const f = rawFiles[i];
      const fPath = String((f && f.file) || "");
      const cStats = (typeof getPgFileCommentStats === "function")
        ? getPgFileCommentStats(num, fPath)
        : _PG_EMPTY_FILE_COMMENT_STATS;
      out[i] = {
        file: fPath,
        status: getPgFileStatus(f),
        additions: Number((f && f.additions) || 0),
        deletions: Number((f && f.deletions) || 0),
        isReviewed: revSet.has(fPath),
        unresolvedCount: Number((cStats && cStats.unresolvedCount) || 0),
        commentCount: Number((cStats && cStats.commentCount) || 0),
        draftCount: Number((cStats && cStats.draftCount) || 0),
        isConflict: conflictSet.size > 0 && conflictSet.has(fPath),
        isBinary: Boolean((f && f.is_binary) || isPgBinaryFile(fPath)),
        isImage: Boolean((f && f.is_image) || isPgImageFile(fPath)),
      };
    }
    return out;
  }

  function updateHeaderCodeViewerButtonDom(clNum, fileCount) {
    const num = Number(clNum);
    if (!num) return;
    const btn = document.getElementById(`btnCodeViewerMode-${num}`);
    if (!btn) return;
    const html = AemuGerritReact.renderPgCodeViewerToggleButtonHtml({
      clNumber: num,
      isActive: Boolean(state.codeViewerMode),
      fileCount: Number(fileCount || 0),
    });
    if (typeof btn.outerHTML === "string") {
      btn.outerHTML = html;
    } else {
      btn.classList.toggle("is-active", Boolean(state.codeViewerMode));
      btn.setAttribute("aria-pressed", state.codeViewerMode ? "true" : "false");
    }
  }

  function clearCodeViewerRightPaneClasses() {
    const views = document.querySelectorAll(".pg-change-view");
    views.forEach(view => {
      view.classList.remove("cv-showing-single-file", "cv-showing-overview", "cv-all-files-mode");
    });
    const hiddenBlocks = document.querySelectorAll(".pg-file-diff-block.cv-hidden-file-block");
    hiddenBlocks.forEach(blk => {
      blk.classList.remove("cv-hidden-file-block");
    });
  }

  function applyCodeViewerSingleFileBlocksDom(commitEl, selectedFile) {
    if (!commitEl || typeof commitEl.querySelectorAll !== "function") return;
    const blocks = commitEl.querySelectorAll(".pg-file-diff-block");
    blocks.forEach(blk => {
      const blkFile = blk.getAttribute("data-file") || "";
      blk.classList.toggle("cv-hidden-file-block", blkFile !== selectedFile);
    });
  }

  function applyCodeViewerRightPaneDom(clNum, curPs, selectedFile, opts = {}) {
    const num = Number(clNum);
    const commitEl = document.getElementById(`pgStackCommit-${num}`);
    if (!commitEl) return;
    const activeTab = (state.activeChangeTabByCl && state.activeChangeTabByCl[num]) || "files";
    if (selectedFile !== CODE_VIEWER_COMMIT_MSG_PATH && activeTab !== "files") {
      selectPgChangeTab(num, "files");
    }
    const mode = state.codeViewerDisplayMode === "all" ? "all" : "single";
    if (mode === "all") {
      commitEl.classList.remove("cv-showing-single-file", "cv-showing-overview");
      commitEl.classList.add("cv-all-files-mode");
      if (typeof commitEl.querySelectorAll === "function") {
        commitEl.querySelectorAll(".pg-file-diff-block.cv-hidden-file-block").forEach(blk => {
          blk.classList.remove("cv-hidden-file-block");
        });
      }
      if (opts.scrollOnSelect) {
        scrollToFileDiff(num, curPs, selectedFile);
      }
      return;
    }

    if (selectedFile === CODE_VIEWER_COMMIT_MSG_PATH) {
      commitEl.classList.remove("cv-showing-single-file", "cv-all-files-mode");
      commitEl.classList.add("cv-showing-overview");
      if (typeof commitEl.querySelectorAll === "function") {
        commitEl.querySelectorAll(".pg-file-diff-block.cv-hidden-file-block").forEach(blk => {
          blk.classList.remove("cv-hidden-file-block");
        });
      }
      uncollapseCommitMessage(num, false);
    } else {
      setPgFileCollapsed(num, curPs, selectedFile, false);
      commitEl.classList.remove("cv-showing-overview", "cv-all-files-mode");
      commitEl.classList.add("cv-showing-single-file");
      applyCodeViewerSingleFileBlocksDom(commitEl, selectedFile);
    }

    if (opts.restoreFileScroll !== false) {
      const diffScroll = document.getElementById("diffScroll");
      if (diffScroll) {
        const key = `${num}:${selectedFile}`;
        const savedTop = (state.codeViewerScrollByFile && state.codeViewerScrollByFile[key]) || 0;
        diffScroll.scrollTop = savedTop;
      }
    }
  }

  function buildCodeViewerSidebarViewModel(num) {
    const c = findPgChangeByNumber(num) || { number: num, patch_set: 1, subject: `ag/${num}` };
    const bundle = (state.diffsByCl && state.diffsByCl[num]) || null;
    const previewBundle = bundle || (Array.isArray(c.files) && c.files.length > 0 ? { files: c.files, patch_set: c.patch_set } : null);
    const curPs = getCodeViewerPatchSet(num, previewBundle, c);
    const isLoadingDiff = !previewBundle || !Array.isArray(previewBundle.files);
    const selectedFile = isLoadingDiff
      ? CODE_VIEWER_COMMIT_MSG_PATH
      : resolveCodeViewerSelectedFile(num, previewBundle);
    const entries = isLoadingDiff ? [] : buildCodeViewerFileEntries(c, previewBundle, curPs);
    const collapsedSet = getCodeViewerCollapsedSet(num);
    const collapsedDirs = collapsedSet.size > 0 ? Array.from(collapsedSet) : [];
    const filterQuery = (state.codeViewerFilterByCl && state.codeViewerFilterByCl[num]) || "";
    const displayMode = state.codeViewerDisplayMode === "all" ? "all" : "single";
    const canNav = canNavigateVisibleCls();
    const cmStats = (typeof getPgFileCommentStats === "function")
      ? getPgFileCommentStats(num, CODE_VIEWER_COMMIT_MSG_PATH)
      : _PG_EMPTY_FILE_COMMENT_STATS;
    const cmUnres = Number((cmStats && cmStats.unresolvedCount) || 0);
    const revSet = getPgReviewedSet(num, curPs);
    const revSig = revSet.size > 0 ? Array.from(revSet).join(",") : "";
    const commentSig = entries.map(e => `${e.unresolvedCount}:${e.draftCount}:${e.commentCount}`).join(",");
    const renderSig = `${num}|${curPs}|${isLoadingDiff ? 1 : 0}|${entries.length}|${selectedFile}|${collapsedDirs.join(",")}|${filterQuery}|${displayMode}|${canNav ? 1 : 0}|${revSig}|${cmUnres}|${commentSig}|${c.subject || ""}`;
    return {
      renderSig,
      props: {
        clNumber: num,
        patchSet: curPs,
        subject: c.subject || "",
        files: entries,
        selectedFile,
        collapsedDirs,
        filterQuery,
        displayMode,
        totalFileCount: entries.length,
        reviewedFileCount: entries.filter(e => e.isReviewed).length,
        commitMsgUnresolvedCount: cmUnres,
        isLoadingDiff,
        canNavigateCommits: canNav,
      },
    };
  }

  function replaceHtmlPreservingTreeFocusAndScroll(sidebarEl, html) {
    const prevInputFocused = Boolean(
      typeof document !== "undefined" &&
      document.activeElement &&
      document.activeElement.id === "cvTreeFilterInput"
    );
    const prevSelStart = prevInputFocused ? document.activeElement.selectionStart : null;
    const prevTreeScrollEl = document.getElementById("cvTreeScroll");
    const prevTreeScroll = prevTreeScrollEl ? prevTreeScrollEl.scrollTop : 0;

    sidebarEl.innerHTML = html;

    const nextTreeScrollEl = document.getElementById("cvTreeScroll");
    if (nextTreeScrollEl && prevTreeScroll > 0) {
      nextTreeScrollEl.scrollTop = prevTreeScroll;
    }
    if (!prevInputFocused) return;
    const nextInp = document.getElementById("cvTreeFilterInput");
    if (!nextInp || typeof nextInp.focus !== "function") return;
    nextInp.focus();
    if (prevSelStart !== null && typeof nextInp.setSelectionRange === "function") {
      nextInp.setSelectionRange(prevSelStart, prevSelStart);
    }
  }

  function renderCodeViewerSidebarDom(opts = {}) {
    const sidebarEl = document.getElementById("codeViewerSidebar");
    if (!sidebarEl) return;
    if (!state.codeViewerMode) {
      sidebarEl.style.display = "none";
      sidebarEl._lastCvRenderSig = "";
      return;
    }
    sidebarEl.style.display = "flex";
    const num = getActiveCodeViewerClNumber();
    if (!num) {
      sidebarEl.innerHTML = `<div class="cv-tree-empty">Select a commit to view changed files.</div>`;
      return;
    }
    const { props, renderSig } = buildCodeViewerSidebarViewModel(num);
    if (!opts.force && sidebarEl._lastCvRenderSig === renderSig && sidebarEl.innerHTML.length > 0) {
      return;
    }
    replaceHtmlPreservingTreeFocusAndScroll(
      sidebarEl,
      AemuGerritReact.renderPgCodeViewerSidebarHtml(props)
    );
    sidebarEl._lastCvRenderSig = renderSig;
  }

  function syncCodeViewerModeDom(opts = {}) {
    const num = getActiveCodeViewerClNumber();
    const bundle = (num && state.diffsByCl && state.diffsByCl[num]) || null;
    const fCount = (bundle && Array.isArray(bundle.files)) ? bundle.files.length : 0;
    syncSidebarLayoutDom();
    updateHeaderCodeViewerButtonDom(num, fCount);
    if (!state.codeViewerMode || isContinuousStackDiffActive()) {
      renderCodeViewerSidebarDom();
      clearCodeViewerRightPaneClasses();
      return;
    }
    renderCodeViewerSidebarDom(opts);
    if (!num || !bundle || !Array.isArray(bundle.files)) {
      return;
    }
    const c = findPgChangeByNumber(num) || { number: num, patch_set: 1 };
    const curPs = getCodeViewerPatchSet(num, bundle, c);
    const selectedFile = resolveCodeViewerSelectedFile(num, bundle);
    updatePgActiveFile(num, selectedFile);
    applyCodeViewerRightPaneDom(num, curPs, selectedFile, opts);
  }

  function togglePgCodeViewerMode(clNum = null, forceMode = undefined) {
    const targetCl = getActiveCodeViewerClNumber(clNum);
    if (clNum && Number(clNum) !== Number(state.selectedClNumber)) {
      const st = findPgStackForClOrId(Number(clNum), state.selectedStackId);
      state.selectedClNumber = Number(clNum);
      if (st) state.selectedStackId = st.stack_id;
    }
    const nextMode = typeof forceMode === "boolean" ? forceMode : !state.codeViewerMode;
    state.codeViewerMode = nextMode;
    if (nextMode) {
      if (state.sidebarCollapsed) {
        state.sidebarCollapsed = false;
      }
      if (state.continuousStackDiff) {
        state.continuousStackDiff = false;
        renderDiffPane({ preserveScroll: false });
        return;
      }
      if (targetCl && (!state.diffsByCl || !state.diffsByCl[targetCl])) {
        ensureActiveDiffsLoaded({ preserveScroll: false });
      }
    }
    syncCodeViewerModeDom({ restoreFileScroll: true });
    if (typeof showToast === "function") {
      showToast(
        nextMode
          ? `Entered Commit Code Viewer mode (\\)`
          : `Returned to Dashboard sidebar (\\)`,
        1500
      );
    }
  }

  function setCodeViewerDisplayMode(mode) {
    const next = mode === "all" ? "all" : "single";
    if (state.codeViewerDisplayMode === next) return;
    state.codeViewerDisplayMode = next;
    syncCodeViewerModeDom({ scrollOnSelect: next === "all", restoreFileScroll: next === "single" });
  }

  function saveCurrentCodeViewerFileScroll(clNum, prevFile) {
    if (!prevFile || state.codeViewerDisplayMode === "all") return;
    const diffScroll = document.getElementById("diffScroll");
    if (!diffScroll) return;
    state.codeViewerScrollByFile = state.codeViewerScrollByFile || {};
    state.codeViewerScrollByFile[`${Number(clNum)}:${prevFile}`] = Number(diffScroll.scrollTop || 0);
  }

  function updateCodeViewerSelectedRowDom(filePath) {
    const sidebarEl = document.getElementById("codeViewerSidebar");
    if (!sidebarEl || typeof sidebarEl.querySelectorAll !== "function") return false;
    const rows = sidebarEl.querySelectorAll(".cv-tree-file-row, .cv-tree-overview-row");
    if (!rows.length) return false;
    rows.forEach(row => {
      const isMatch = (row.getAttribute("data-cv-file") || "") === filePath;
      row.classList.toggle("is-selected", isMatch);
      row.setAttribute("aria-selected", isMatch ? "true" : "false");
    });
    return true;
  }

  function selectCodeViewerFile(clNum, patchSet, filePath, event = null) {
    if (event) {
      if (event.target && typeof event.target.closest === "function" && event.target.closest(".cv-reviewed-toggle")) {
        return;
      }
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const num = getActiveCodeViewerClNumber(clNum);
    if (!num) return;
    const ps = Number(patchSet || 1);
    const nextFile = String(filePath || CODE_VIEWER_COMMIT_MSG_PATH);
    state.codeViewerSelectedFileByCl = state.codeViewerSelectedFileByCl || {};
    const prevFile = state.codeViewerSelectedFileByCl[num] || "";
    if (prevFile && prevFile !== nextFile) {
      saveCurrentCodeViewerFileScroll(num, prevFile);
    }
    state.codeViewerSelectedFileByCl[num] = nextFile;
    updatePgActiveFile(num, nextFile);
    if (!updateCodeViewerSelectedRowDom(nextFile)) {
      renderCodeViewerSidebarDom({ force: true });
    } else {
      const sidebarEl = document.getElementById("codeViewerSidebar");
      if (sidebarEl) sidebarEl._lastCvRenderSig = "";
    }
    applyCodeViewerRightPaneDom(num, ps, nextFile, {
      scrollOnSelect: true,
      restoreFileScroll: true,
    });
  }

  function collectVisibleCodeViewerFiles(clNum, bundle) {
    const sidebarEl = document.getElementById("codeViewerSidebar");
    if (sidebarEl && typeof sidebarEl.querySelectorAll === "function") {
      const domRows = sidebarEl.querySelectorAll(".cv-tree-file-row");
      if (domRows && domRows.length > 0) {
        return Array.from(domRows).map(r => r.getAttribute("data-cv-file") || "").filter(Boolean);
      }
    }
    const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : _PG_EMPTY_ARR;
    const c = findPgChangeByNumber(clNum);
    const sorted = (rawFiles.length > 0 && typeof getPgSortedFileList === "function")
      ? getPgSortedFileList(c || { number: clNum }, rawFiles)
      : rawFiles;
    return sorted.map(f => f && f.file).filter(Boolean);
  }

  function stepCodeViewerFile(dir, fromFile = null, fromCl = null) {
    const num = getActiveCodeViewerClNumber(fromCl);
    if (!num) return;
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const files = collectVisibleCodeViewerFiles(num, bundle);
    if (files.length === 0) return;
    const curFile = fromFile || (state.codeViewerSelectedFileByCl && state.codeViewerSelectedFileByCl[num]) || files[0];
    const idx = files.indexOf(curFile);
    let nextIdx;
    if (idx < 0) {
      nextIdx = dir === "prev" ? files.length - 1 : 0;
    } else {
      nextIdx = dir === "prev"
        ? (idx - 1 + files.length) % files.length
        : (idx + 1) % files.length;
    }
    const curPs = getCodeViewerPatchSet(num, bundle);
    selectCodeViewerFile(num, curPs, files[nextIdx]);
  }

  function toggleCodeViewerDir(clNum, dirPath) {
    const num = getActiveCodeViewerClNumber(clNum);
    if (!num || !dirPath) return;
    const set = getCodeViewerCollapsedSet(num);
    if (set.has(dirPath)) {
      set.delete(dirPath);
    } else {
      set.add(dirPath);
    }
    renderCodeViewerSidebarDom({ force: true });
  }

  function collectAllCodeViewerDirPaths(nodes, out) {
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.dirPath) out.push(n.dirPath);
      if (Array.isArray(n.children) && n.children.length > 0) {
        collectAllCodeViewerDirPaths(n.children, out);
      }
    }
  }

  function expandAllCodeViewerDirs(clNum) {
    const num = getActiveCodeViewerClNumber(clNum);
    if (!num) return;
    getCodeViewerCollapsedSet(num).clear();
    renderCodeViewerSidebarDom({ force: true });
  }

  function collapseAllCodeViewerDirs(clNum) {
    const num = getActiveCodeViewerClNumber(clNum);
    if (!num) return;
    const c = findPgChangeByNumber(num) || { number: num };
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const entries = buildCodeViewerFileEntries(c, bundle, getCodeViewerPatchSet(num, bundle, c));
    const tree = AemuGerritReact.buildCodeViewerDirectoryTree(entries);
    const paths = [];
    collectAllCodeViewerDirPaths(tree.children || [], paths);
    const set = getCodeViewerCollapsedSet(num);
    set.clear();
    for (let i = 0; i < paths.length; i++) {
      set.add(paths[i]);
    }
    renderCodeViewerSidebarDom({ force: true });
  }

  function onCodeViewerFilterInput(val) {
    const num = getActiveCodeViewerClNumber();
    if (!num) return;
    state.codeViewerFilterByCl = state.codeViewerFilterByCl || {};
    state.codeViewerFilterByCl[num] = String(val || "");
    renderCodeViewerSidebarDom({ force: true });
  }

  function clearCodeViewerFilter() {
    const num = getActiveCodeViewerClNumber();
    if (!num) return;
    state.codeViewerFilterByCl = state.codeViewerFilterByCl || {};
    state.codeViewerFilterByCl[num] = "";
    renderCodeViewerSidebarDom({ force: true });
    const inp = document.getElementById("cvTreeFilterInput");
    if (inp && typeof inp.focus === "function") inp.focus();
  }

  function onCodeViewerFilterKeyDown(event) {
    if (!event) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      const num = getActiveCodeViewerClNumber();
      const cur = (num && state.codeViewerFilterByCl && state.codeViewerFilterByCl[num]) || "";
      if (cur) {
        clearCodeViewerFilter();
      } else if (event.target && typeof event.target.blur === "function") {
        event.target.blur();
      }
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "Enter") return;
    event.preventDefault();
    const sidebarEl = document.getElementById("codeViewerSidebar");
    const firstRow = sidebarEl && sidebarEl.querySelector
      ? sidebarEl.querySelector(".cv-tree-file-row, .cv-tree-overview-row")
      : null;
    if (!firstRow) return;
    if (typeof firstRow.focus === "function") firstRow.focus();
    const fPath = firstRow.getAttribute("data-cv-file");
    const num = getActiveCodeViewerClNumber();
    if (!fPath || !num) return;
    const bundle = state.diffsByCl && state.diffsByCl[num];
    selectCodeViewerFile(num, getCodeViewerPatchSet(num, bundle), fPath);
  }

  function onCodeViewerFileKeyDown(event, clNum, patchSet, filePath) {
    if (!event) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      selectCodeViewerFile(clNum, patchSet, filePath);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "j") {
      event.preventDefault();
      event.stopPropagation();
      stepCodeViewerFile("next", filePath, clNum);
      return;
    }
    if (event.key === "ArrowUp" || event.key === "k") {
      event.preventDefault();
      event.stopPropagation();
      stepCodeViewerFile("prev", filePath, clNum);
      return;
    }
    if ((event.key === "f" || event.key === "F") && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      event.stopPropagation();
      toggleCodeViewerFileReviewed(event, clNum, patchSet, filePath);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      togglePgCodeViewerMode(clNum, false);
    }
  }

  function onCodeViewerDirKeyDown(event, clNum, dirPath) {
    if (!event) return;
    if (event.key === "Enter" || event.key === " " || event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      event.stopPropagation();
      toggleCodeViewerDir(clNum, dirPath);
    }
  }

  function toggleCodeViewerFileReviewed(event, clNum, patchSet, filePath) {
    if (event) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    const num = getActiveCodeViewerClNumber(clNum);
    const ps = Number(patchSet || 1);
    const pathStr = String(filePath || "");
    if (!num || !pathStr || pathStr === CODE_VIEWER_COMMIT_MSG_PATH) return;
    const revSet = getPgReviewedSet(num, ps);
    const nextReviewed = !revSet.has(pathStr);
    setPgFileReviewed(num, ps, pathStr, nextReviewed);
  }

  function syncCodeViewerReviewedDom(clNum, curPs, filePath, isReviewed) {
    const sidebarEl = document.getElementById("codeViewerSidebar");
    if (!sidebarEl) return;
    sidebarEl._lastCvRenderSig = "";
    if (typeof sidebarEl.querySelectorAll === "function") {
      sidebarEl.querySelectorAll(".cv-tree-file-row").forEach(row => {
        if ((row.getAttribute("data-cv-file") || "") !== filePath) return;
        row.classList.toggle("is-reviewed", isReviewed);
        const btn = row.querySelector && row.querySelector(".cv-reviewed-toggle");
        if (btn) btn.classList.toggle("is-checked", isReviewed);
      });
    }
    const pill = document.getElementById(`cvReviewedSummary-${Number(clNum)}`);
    const bundle = state.diffsByCl && state.diffsByCl[Number(clNum)];
    if (pill && bundle && Array.isArray(bundle.files)) {
      const revSet = getPgReviewedSet(Number(clNum), Number(curPs || 1));
      let revCnt = 0;
      for (let i = 0; i < bundle.files.length; i++) {
        if (bundle.files[i] && revSet.has(bundle.files[i].file)) revCnt++;
      }
      const allDone = bundle.files.length > 0 && revCnt === bundle.files.length;
      pill.textContent = `${revCnt}/${bundle.files.length} ✓`;
      pill.classList.toggle("is-complete", allDone);
      pill.classList.toggle("all-reviewed", allDone);
    }
  }

  if (typeof window !== "undefined") {
    window.getClVerifiedDisplay = getClVerifiedDisplay;
    window.renderPgVVoteBodyHtml = renderPgVVoteBodyHtml;
    window.renderPgVVoteCellHtml = renderPgVVoteCellHtml;
    window.renderPgColHeaderRowHtml = renderPgColHeaderRowHtml;
    window.isClMerged = isClMerged;
    window.isClAbandoned = isClAbandoned;
    window.isClClosed = isClClosed;
    window.isClViewerCcOnly = isClViewerCcOnly;
    window.getScopeDisplayLabel = getScopeDisplayLabel;
    window.isFullVisibilityScope = isFullVisibilityScope;
    window.computePgHeaderStatusInfo = computePgHeaderStatusInfo;
    window.isClConflict = isClConflict;
    window.fetchPgMergeable = fetchPgMergeable;
    window.loadPgMergeable = loadPgMergeable;
    window.syncPgClMergeability = syncPgClMergeability;
    window.refreshPgMergeableStatus = refreshPgMergeableStatus;
    window.renderPgStatusTagHtml = renderPgStatusTagHtml;
    window.partitionCommitsIntoDashboardSections = partitionCommitsIntoDashboardSections;
    window.isClStarred = isClStarred;
    window.togglePgStarChange = togglePgStarChange;
    window.openPgSearchSuggestions = openPgSearchSuggestions;
    window.closePgSearchSuggestions = closePgSearchSuggestions;
    window.togglePgSearchSuggestions = togglePgSearchSuggestions;
    window.renderPgSearchSuggestionsPopoverHtml = renderPgSearchSuggestionsPopoverHtml;
    window.applyPgSearchSuggestion = applyPgSearchSuggestion;
    window.togglePgSearchPresetToken = togglePgSearchPresetToken;
    window.renderPgSearchPresetsHtml = renderPgSearchPresetsHtml;
    window.renderPgSearchPresetsDom = renderPgSearchPresetsDom;
    window.getPgFilteredSearchSuggestions = getPgFilteredSearchSuggestions;
    window.matchesSearchQuery = matchesSearchQuery;
    window.evaluatePgLabelFilter = evaluatePgLabelFilter;
    window.highlightPgShortcutText = highlightPgShortcutText;
    window.getPgShortcutsCategoryCounts = getPgShortcutsCategoryCounts;
    window.renderPgShortcutsCategoryBarHtml = renderPgShortcutsCategoryBarHtml;
    window.updatePgShortcutsMatchCount = updatePgShortcutsMatchCount;
    window.selectPgShortcutsCategory = selectPgShortcutsCategory;
    window.syncPgShortcutsDialogView = syncPgShortcutsDialogView;
    window.navigatePgShortcutsMatches = navigatePgShortcutsMatches;
    window.renderPgShortcutsHtml = renderPgShortcutsHtml;
    window.openShortcutsDialog = openShortcutsDialog;
    window.closeShortcutsDialog = closeShortcutsDialog;
    window.toggleShortcutsDialog = toggleShortcutsDialog;
    window.clearShortcutsSearch = clearShortcutsSearch;
    window.PG_KEYBOARD_SHORTCUTS = PG_KEYBOARD_SHORTCUTS;
    window.formatCommitMessageHtml = formatCommitMessageHtml;
    window.renderInlineMarkdownHtml = renderInlineMarkdownHtml;
    window._isFooterLine = _isFooterLine;
    window._renderFooterLineHtml = _renderFooterLineHtml;
    window.copyPgTrailerValue = copyPgTrailerValue;
    window.filterDashboardByChangeId = filterDashboardByChangeId;
    window.onPgChangeIdLinkClick = onPgChangeIdLinkClick;
    window.onPgAuthorEmailClick = onPgAuthorEmailClick;
    window.openPgRebaseDialog = openPgRebaseDialog;
    window.getPgChangeDescendants = getPgChangeDescendants;
    window.getPgRebaseCandidateBases = getPgRebaseCandidateBases;
    window.renderPgRebaseSuggestions = renderPgRebaseSuggestions;
    window.selectPgRebaseCustomBase = selectPgRebaseCustomBase;
    window.filterPgRebaseSuggestions = filterPgRebaseSuggestions;
    window.onPgRebaseCustomBaseInput = onPgRebaseCustomBaseInput;
    window.getPgCherryPickCandidateBranches = getPgCherryPickCandidateBranches;
    window.renderPgCherryPickBranchSuggestions = renderPgCherryPickBranchSuggestions;
    window.onPgCherryPickBranchInput = onPgCherryPickBranchInput;
    window.updatePgCherryPickSameBranchWarning = updatePgCherryPickSameBranchWarning;
    window.getPgRecentCherryPickBranches = getPgRecentCherryPickBranches;
    window.savePgRecentCherryPickBranch = savePgRecentCherryPickBranch;
    window.getPgCherryPickCandidateBases = getPgCherryPickCandidateBases;
    window.renderPgCherryPickBaseSuggestions = renderPgCherryPickBaseSuggestions;
    window.selectPgCherryPickCustomBase = selectPgCherryPickCustomBase;
    window.onPgCherryPickBaseInput = onPgCherryPickBaseInput;
    window.openPgSearchHelpDialog = openPgSearchHelpDialog;
    window.closePgSearchHelpDialog = closePgSearchHelpDialog;
    window.togglePgSearchHelpDialog = togglePgSearchHelpDialog;
    window.onPgSearchHelpBackdropClick = onPgSearchHelpBackdropClick;
    window.onPgSearchHelpFilterInput = onPgSearchHelpFilterInput;
    window.clearPgSearchHelpFilter = clearPgSearchHelpFilter;
    window.selectPgSearchHelpCategory = selectPgSearchHelpCategory;
    window.insertPgSearchOperator = insertPgSearchOperator;
    window.insertPgSearchOperatorByIdx = insertPgSearchOperatorByIdx;
    window.runPgSearchExample = runPgSearchExample;
    window.runPgSearchExampleByIdx = runPgSearchExampleByIdx;
    window.copyPgSearchOperator = copyPgSearchOperator;
    window.copyPgSearchOperatorByIdx = copyPgSearchOperatorByIdx;
    window.renderPgSearchHelpCategoryChipsHtml = renderPgSearchHelpCategoryChipsHtml;
    window.renderPgSearchHelpGridHtml = renderPgSearchHelpGridHtml;
    window.splitTopLevelOr = splitTopLevelOr;
    window.splitTopLevelTokens = splitTopLevelTokens;
    window.evaluatePgAtomicSearchToken = evaluatePgAtomicSearchToken;
    window.PG_SEARCH_HELP_OPERATORS = PG_SEARCH_HELP_OPERATORS;
    window.renderPgReplyMarkdownToolbarHtml = renderPgReplyMarkdownToolbarHtml;
    window.formatPgReplyCommentMarkdown = formatPgReplyCommentMarkdown;
    window.setPgReplyMessageTab = setPgReplyMessageTab;
    window.getPgReplyMessageTab = getPgReplyMessageTab;
    window.togglePgReplyMessageTab = togglePgReplyMessageTab;
    window.renderPgReplyMessageMarkdownHtml = renderPgReplyMessageMarkdownHtml;
    window.renderPgReplyMessageLivePreview = renderPgReplyMessageLivePreview;
    window.renderPgReplyDialogHtml = renderPgReplyDialogHtml;
    window.renderBatchModePanelHtml = renderBatchModePanelHtml;
    window.selectAllBatchCls = selectAllBatchCls;
    window.toggleSelectAllBatchCls = toggleSelectAllBatchCls;
    window.invertBatchClsSelection = invertBatchClsSelection;
    window.renderQueueBatchSelectStripHtml = renderQueueBatchSelectStripHtml;
    window.renderBatchReviewerPopoverDom = renderBatchReviewerPopoverDom;
    window.renderPgLabelScoresTableHtml = renderPgLabelScoresTableHtml;
    window.isRestrictedNonVotingLabel = isRestrictedNonVotingLabel;
    window.isPgSrNaExpanded = isPgSrNaExpanded;
    window.togglePgSrNaList = togglePgSrNaList;
    window.renderPgSubmitRequirementsHtml = renderPgSubmitRequirementsHtml;
    window.renderInViewerStagedVoteRowHtml = renderInViewerStagedVoteRowHtml;
    window.toggleStagedCr = toggleStagedCr;
    window.toggleStagedVerified = toggleStagedVerified;
    window.toggleStagedPresubmitVerified = toggleStagedPresubmitVerified;
    window.setStagedPrValue = setStagedPrValue;
    window.toggleStagedPr = toggleStagedPr;
    window.toggleStagedAs = toggleStagedAs;
    window.toggleStagedPerformance = toggleStagedPerformance;
    window.toggleStagedLint = toggleStagedLint;
    window.toggleStagedBypassPresubmit = toggleStagedBypassPresubmit;
    window.toggleStagedApiReview = toggleStagedApiReview;
    window.toggleStagedOwnersOverride = toggleStagedOwnersOverride;
    window.toggleStagedRubberStamp = toggleStagedRubberStamp;
    window.toggleStagedCommitQueue = toggleStagedCommitQueue;
    window.toggleStagedOpenSourceLicensing = toggleStagedOpenSourceLicensing;
    window.toggleStagedTrust = toggleStagedTrust;
    window.toggleStagedCustomLabel = toggleStagedCustomLabel;
    window.toggleExtendedVoteControls = toggleExtendedVoteControls;
    window.resetStagedVote = resetStagedVote;
    window.syncActiveActionButtons = syncActiveActionButtons;
    window.computeStagedVotePayload = computeStagedVotePayload;
    window.toggleClCheckbox = toggleClCheckbox;
    window.clearCheckedCls = clearCheckedCls;
    window.castVotes = castVotes;
    window.formatVoteLabelsText = formatVoteLabelsText;
    window.markClsVotedOptimistically = markClsVotedOptimistically;
    window.applyOptimisticVoteToCl = applyOptimisticVoteToCl;
    window.clampSidebarWidth = clampSidebarWidth;
    window.syncSidebarLayoutDom = syncSidebarLayoutDom;
    window.isPgFileBlameActive = isPgFileBlameActive;
    window.setPgFileBlameActive = setPgFileBlameActive;
    window.togglePgFileBlame = togglePgFileBlame;
    window.buildPgLineBlameMap = buildPgLineBlameMap;
    window.renderPgBlameCellHtml = renderPgBlameCellHtml;
    window.applyPgBlameToDomTable = applyPgBlameToDomTable;
    window.onPgBlameCellMouseEnter = onPgBlameCellMouseEnter;
    window.onPgBlameCellMouseLeave = onPgBlameCellMouseLeave;
    window.normalizePgBugId = normalizePgBugId;
    window.formatBugStatusOrType = formatBugStatusOrType;
    window.removeGoogleDomain = removeGoogleDomain;
    window.resolvePgBugComponentFullName = resolvePgBugComponentFullName;
    window.getPgBugStatusClass = getPgBugStatusClass;
    window.formatPgBugTimestampShort = formatPgBugTimestampShort;
    window.resolvePgBugMetadata = resolvePgBugMetadata;
    window.renderPgBugHovercardContentHtml = renderPgBugHovercardContentHtml;
    window.ensurePgBugHovercardEl = ensurePgBugHovercardEl;
    window.positionPgBugHovercard = positionPgBugHovercard;
    window.fetchPgBugDetailsAsync = fetchPgBugDetailsAsync;
    window.showPgBugHovercard = showPgBugHovercard;
    window.hidePgBugHovercard = hidePgBugHovercard;
    window.scheduleHidePgBugHovercard = scheduleHidePgBugHovercard;
    window.cancelHidePgBugHovercard = cancelHidePgBugHovercard;
    window.copyPgBugHovercardId = copyPgBugHovercardId;
    window.filterPgByBugId = filterPgByBugId;
    window.onPgBugRelatedClsClick = onPgBugRelatedClsClick;
    window.onPgBugCardClClick = onPgBugCardClClick;
    window.pgBugDetailsCache = pgBugDetailsCache;
    window.setPgStylePropertyIfChanged = setPgStylePropertyIfChanged;
    window.recordPgScrollActivity = recordPgScrollActivity;
    window.isPgActivelyScrolling = isPgActivelyScrolling;
    window.isPgSidebarScrollTarget = isPgSidebarScrollTarget;
    window.schedulePgDiffScrollSync = schedulePgDiffScrollSync;
    window.handlePgWindowScroll = handlePgWindowScroll;
    window.tryFastSelectClInQueueDom = tryFastSelectClInQueueDom;
    window.computePgDashboardRowCacheKey = computePgDashboardRowCacheKey;
    window.getPgFileLinesVersionSig = getPgFileLinesVersionSig;
    window.computePgFileCodeLinesCacheKey = computePgFileCodeLinesCacheKey;
    window.computePgFileOwnerStatusCacheKey = computePgFileOwnerStatusCacheKey;
    window.canNavigateVisibleCls = canNavigateVisibleCls;
    window._pgHighlightedLineHtmlCache = _pgHighlightedLineHtmlCache;
    window.decorateDiffLineSlice = decorateDiffLineSlice;
    window.extractSymbolFromCodeLine = extractSymbolFromCodeLine;
    window.findPgStackForClOrId = findPgStackForClOrId;
    window.getClSearchHaystack = getClSearchHaystack;
    window.parsePgSearchQueryAst = parsePgSearchQueryAst;
    window.evalPgSearchQueryAst = evalPgSearchQueryAst;
    window._pgSearchQueryAstCache = _pgSearchQueryAstCache;
    window.togglePgCodeViewerMode = togglePgCodeViewerMode;
    window.setCodeViewerDisplayMode = setCodeViewerDisplayMode;
    window.selectCodeViewerFile = selectCodeViewerFile;
    window.stepCodeViewerFile = stepCodeViewerFile;
    window.toggleCodeViewerDir = toggleCodeViewerDir;
    window.expandAllCodeViewerDirs = expandAllCodeViewerDirs;
    window.collapseAllCodeViewerDirs = collapseAllCodeViewerDirs;
    window.onCodeViewerFilterInput = onCodeViewerFilterInput;
    window.clearCodeViewerFilter = clearCodeViewerFilter;
    window.onCodeViewerFilterKeyDown = onCodeViewerFilterKeyDown;
    window.onCodeViewerFileKeyDown = onCodeViewerFileKeyDown;
    window.onCodeViewerDirKeyDown = onCodeViewerDirKeyDown;
    window.toggleCodeViewerFileReviewed = toggleCodeViewerFileReviewed;
    window.syncCodeViewerModeDom = syncCodeViewerModeDom;
  }

  applyPgDiffPreferences();
  state.showResolvedComments = isPgShowResolvedCommentsEnabled();
  state.showRobotComments = isPgShowRobotCommentsEnabled();
  loadSidebarLayoutPrefs();
  initWorkspaceSplitter();
  initDiffCodeSelectionListeners();
  initPgFileListRangeDragListeners();
  initPgDashboardRangeDragListeners();
  initImagePanListeners();
  initApp();
