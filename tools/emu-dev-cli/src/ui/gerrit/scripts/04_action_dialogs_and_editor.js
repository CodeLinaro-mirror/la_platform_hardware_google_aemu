  // --- PolyGerrit Action Dialogs (Download, Rebase, Abandon, Restore) & File Shortcuts ---

  function resolvePgChangeForAction(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    let c = findChangeByNumber(num);
    if (!c && Array.isArray(state.stacks)) {
      for (const st of state.stacks) {
        if (Array.isArray(st.commits)) {
          c = st.commits.find(x => Number(x.number) === num);
          if (c) break;
        }
      }
    }
    return c;
  }

  function computePgDownloadCommands(c, scheme = null, patchSetNum = null, options = null) {
    if (!c) return {};
    const cl = Number(c.number);
    const maxPs = Number(c.patch_set || 1);
    const ps = Number(
      patchSetNum ||
      (typeof state !== "undefined" && state.downloadPatchset) ||
      (typeof state !== "undefined" && state.selectedPatchsetByCl && state.selectedPatchsetByCl[cl]) ||
      maxPs
    );
    const proj = c.project || "platform/hardware/google/aemu";
    const clStr = String(cl);
    const suffix = clStr.length >= 2 ? clStr.slice(-2) : clStr.padStart(2, "0");
    const rawHost = c.host || (c.url ? (c.url.split("/c/")[0] || "") : "");
    const host = rawHost ? rawHost.replace(/\/+$/, "") : "https://android-review.googlesource.com";
    const ref = `refs/changes/${suffix}/${cl}/${ps}`;
    const activeScheme = scheme || (typeof state !== "undefined" && state.downloadScheme) || "repo";

    const opts = options || {};
    const branchOpt = (opts.branchName !== undefined)
      ? opts.branchName
      : ((typeof state !== "undefined" && state.downloadBranchName) || "");
    const useBranch = (opts.useBranch !== undefined)
      ? !!opts.useBranch
      : (typeof state !== "undefined" && !!state.downloadUseBranch);
    const repoCreateBranch = (opts.repoCreateBranch !== undefined)
      ? !!opts.repoCreateBranch
      : (typeof state !== "undefined" && !!state.downloadRepoCreateBranch);

    const safeBranch = String(branchOpt || "").trim() || `review/${cl}`;

    let gitRemote = `${host}/${proj}`;
    if (activeScheme === "sso") {
      const ssoProj = proj.replace(/^\/+/, "");
      gitRemote = `sso://android/${ssoProj}`;
    } else if (activeScheme === "ssh") {
      const hostDomain = host.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
      const user = (typeof state !== "undefined" && state.viewer) ? (state.viewer.split("@")[0] + "@") : "";
      gitRemote = `ssh://${user}${hostDomain}:29418/${proj}`;
    } else if (activeScheme === "jj") {
      gitRemote = `${host}/${proj}`;
    }

    const checkoutCmd = useBranch
      ? `git fetch ${gitRemote} ${ref} && git checkout -b ${safeBranch} FETCH_HEAD`
      : `git fetch ${gitRemote} ${ref} && git checkout FETCH_HEAD`;
    const cherryPickCmd = `git fetch ${gitRemote} ${ref} && git cherry-pick FETCH_HEAD`;
    const formatPatchCmd = `git fetch ${gitRemote} ${ref} && git format-patch -1 --stdout FETCH_HEAD`;
    const pullCmd = `git pull ${gitRemote} ${ref}`;
    const fetchCmd = `git fetch ${gitRemote} ${ref}`;
    const patchCmd = `curl -Lo- --netrc "${host}/changes/${cl}/revisions/${ps}/patch" | base64 -d | git apply`;

    let repoCmd = `repo download ${proj} ${cl}/${ps}`;
    if (repoCreateBranch) {
      repoCmd = `repo download -c ${proj} ${cl}/${ps}`;
    } else if (useBranch) {
      repoCmd = `repo download -b ${safeBranch} ${proj} ${cl}/${ps}`;
    }

    const jjCheckoutCmd = useBranch
      ? `jj git fetch --remote ${gitRemote} && jj new ${ref} && jj bookmark create ${safeBranch}`
      : `jj git fetch --remote ${gitRemote} && jj new ${ref}`;
    const jjCherryPickCmd = `jj duplicate ${ref}`;
    const jjPullCmd = `jj git fetch --remote ${gitRemote}`;
    const jjShowCmd = `jj show ${ref}`;

    return {
      scheme: activeScheme,
      ref,
      project: proj,
      patchSet: ps,
      maxPatchSet: maxPs,
      host,
      repo: repoCmd,
      checkout: activeScheme === "jj" ? jjCheckoutCmd : checkoutCmd,
      "cherry-pick": activeScheme === "jj" ? jjCherryPickCmd : cherryPickCmd,
      "format-patch": activeScheme === "jj" ? jjShowCmd : formatPatchCmd,
      pull: activeScheme === "jj" ? jjPullCmd : pullCmd,
      fetch: fetchCmd,
      patch: patchCmd,
      jj: jjCheckoutCmd,
      branchName: safeBranch,
      useBranch,
      repoCreateBranch,
    };
  }

  function openPgDownloadDialog(clNum, psNum = null) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    const maxPs = Number(c.patch_set || 1);
    const curPs = Number(
      psNum ||
      (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) ||
      state.downloadPatchset ||
      maxPs
    );
    state.downloadDialogOpen = true;
    state.downloadDialogCl = num;
    state.downloadPatchset = curPs;
    state.downloadActiveTab = state.downloadActiveTab || "repo";
    state.downloadScheme = state.downloadScheme || "repo";
    if (!state.downloadBranchName) {
      state.downloadBranchName = `review/${num}`;
    }

    const backdrop = document.getElementById("pgDownloadBackdrop");
    const titleEl = document.getElementById("pgDownloadTitle");
    const subEl = document.getElementById("pgDownloadSubtitle");
    if (titleEl) titleEl.textContent = `Download Patch · ag/${num}`;
    if (subEl) subEl.textContent = `${c.subject || ""} (Patchset ${curPs})`;

    // Populate patchset selector
    const psSelect = document.getElementById("pgDownloadPatchsetSelect");
    if (psSelect) {
      let psOpts = "";
      for (let p = maxPs; p >= 1; p--) {
        const isSel = p === curPs;
        const tag = p === maxPs ? " (latest)" : "";
        psOpts += `<option value="${p}"${isSel ? " selected" : ""}>PS ${p}${tag}</option>`;
      }
      psSelect.innerHTML = psOpts;
    }

    // Branch customization controls
    const branchCheck = document.getElementById("pgDownloadBranchCheck");
    const branchInp = document.getElementById("pgDownloadBranchInput");
    const repoBranchCheck = document.getElementById("pgDownloadRepoBranchCheck");
    if (branchCheck) branchCheck.checked = !!state.downloadUseBranch;
    if (branchInp) {
      branchInp.value = state.downloadBranchName || `review/${num}`;
      branchInp.disabled = !state.downloadUseBranch;
    }
    if (repoBranchCheck) repoBranchCheck.checked = !!state.downloadRepoCreateBranch;

    const cmds = computePgDownloadCommands(c, state.downloadScheme, curPs);
    const refEl = document.getElementById("pgDownloadRefText");
    const branchEl = document.getElementById("pgDownloadBranchText");
    const projEl = document.getElementById("pgDownloadProjectText");
    if (refEl) refEl.textContent = cmds.ref || `refs/changes/${num}/${curPs}`;
    if (branchEl) branchEl.textContent = c.branch || "main";
    if (projEl) projEl.textContent = c.project || "platform/hardware/google/aemu";

    const schemeBtns = document.querySelectorAll(".pg-download-scheme-chip");
    schemeBtns.forEach(btn => {
      const match = btn.getAttribute("data-scheme") === state.downloadScheme;
      btn.classList.toggle("active", match);
      btn.setAttribute("aria-pressed", match ? "true" : "false");
    });

    selectPgDownloadTab(state.downloadActiveTab);

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
  }

  function setPgDownloadPatchset(psNum) {
    if (!psNum) return;
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    const maxPs = Number(c.patch_set || 1);
    const p = Math.max(1, Math.min(Number(psNum), maxPs));
    state.downloadPatchset = p;

    const psSelect = document.getElementById("pgDownloadPatchsetSelect");
    if (psSelect && Number(psSelect.value) !== p) {
      psSelect.value = String(p);
    }

    const subEl = document.getElementById("pgDownloadSubtitle");
    if (subEl) subEl.textContent = `${c.subject || ""} (Patchset ${p})`;

    const cmds = computePgDownloadCommands(c, state.downloadScheme, p);
    const refEl = document.getElementById("pgDownloadRefText");
    if (refEl) refEl.textContent = cmds.ref;

    selectPgDownloadTab(state.downloadActiveTab || "repo");
    showToast(`Patchset ${p} selected for download`);
  }

  function stepPgDownloadPatchset(delta) {
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    const maxPs = Number(c.patch_set || 1);
    const curPs = Number(
      state.downloadPatchset ||
      (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) ||
      maxPs
    );
    const nextPs = Math.max(1, Math.min(maxPs, curPs + delta));
    if (nextPs !== curPs) {
      setPgDownloadPatchset(nextPs);
    }
  }

  function togglePgDownloadBranchOption(enabled) {
    state.downloadUseBranch = !!enabled;
    const branchInp = document.getElementById("pgDownloadBranchInput");
    if (branchInp) branchInp.disabled = !enabled;
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    if (c) selectPgDownloadTab(state.downloadActiveTab || "repo");
  }

  function onPgDownloadBranchInputChange(val) {
    state.downloadBranchName = String(val || "").trim();
    if (state.downloadUseBranch) {
      const num = state.downloadDialogCl || state.selectedClNumber;
      const c = resolvePgChangeForAction(num);
      if (c) selectPgDownloadTab(state.downloadActiveTab || "repo");
    }
  }

  function togglePgDownloadRepoBranchOption(enabled) {
    state.downloadRepoCreateBranch = !!enabled;
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    if (c) selectPgDownloadTab(state.downloadActiveTab || "repo");
  }

  function closePgDownloadDialog() {
    state.downloadDialogOpen = false;
    state.downloadDialogCl = null;
    const backdrop = document.getElementById("pgDownloadBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgDownloadBackdropClick(e) {
    if (e.target && e.target.id === "pgDownloadBackdrop") {
      closePgDownloadDialog();
    }
  }

  function setPgDownloadScheme(scheme) {
    if (!scheme) return;
    state.downloadScheme = scheme;
    if (typeof localStorage !== "undefined") {
      try { localStorage.setItem("pg_download_scheme", scheme); } catch (_e) {}
    }
    const schemeBtns = document.querySelectorAll(".pg-download-scheme-chip");
    schemeBtns.forEach(btn => {
      const match = btn.getAttribute("data-scheme") === scheme;
      btn.classList.toggle("active", match);
      btn.setAttribute("aria-pressed", match ? "true" : "false");
    });
    selectPgDownloadTab(state.downloadActiveTab || "repo");
    showToast(`Download scheme: ${scheme.toUpperCase()}`);
  }

  function cyclePgDownloadScheme() {
    const schemes = ["repo", "http", "sso", "ssh", "jj"];
    const cur = state.downloadScheme || "repo";
    const idx = schemes.indexOf(cur);
    const next = schemes[(idx + 1) % schemes.length];
    setPgDownloadScheme(next);
  }

  function renderPgDownloadAllCommandsHtml(c, scheme) {
    if (!c) return "";
    const ps = (typeof state !== "undefined" && state.downloadPatchset) || Number(c.patch_set || 1);
    const cmds = computePgDownloadCommands(c, scheme, ps);
    const commandRows = [
      { key: "repo", label: "Repo Download", desc: "Download in repo checkout" },
      { key: "checkout", label: "Checkout", desc: "Fetch and checkout" + (cmds.useBranch ? ` to branch ${cmds.branchName}` : " FETCH_HEAD") },
      { key: "cherry-pick", label: "Cherry Pick", desc: "Apply on current branch" },
      { key: "format-patch", label: "Format Patch", desc: "Create git format patch or jj show" },
      { key: "pull", label: "Pull", desc: "Fetch and merge to branch" },
      { key: "patch", label: "Patch Apply", desc: "Pipe curl base64 to git apply" },
      { key: "fetch", label: "Git Fetch", desc: "Fetch ref without checkout" },
    ];
    if (scheme === "jj") {
      commandRows.unshift({ key: "jj", label: "Jujutsu (JJ)", desc: "Fetch and create new change on ref" });
    }

    return commandRows.map(row => {
      const val = cmds[row.key] || "";
      return `<div class="pg-download-cmd-row">
        <div class="pg-download-cmd-row-header">
          <span>${escapeHtml(row.label)}</span>
          <span class="pg-download-cmd-row-desc">${escapeHtml(row.desc)}</span>
        </div>
        <div class="pg-download-cmd-row-box">
          <input
            type="text"
            class="pg-download-cmd-row-input"
            readonly
            spellcheck="false"
            value="${escapeHtml(val)}"
            onclick="this.select()"
            aria-label="${escapeHtml(row.label)} command"
          />
          <button
            type="button"
            class="pg-download-cmd-row-copy-btn"
            id="btnCopyCmdRow-${row.key}"
            onclick="copyPgSpecificDownloadCommand('${row.key}')"
            title="Copy ${escapeHtml(row.label)} command"
          >📋 Copy</button>
        </div>
      </div>`;
    }).join("");
  }

  function selectPgDownloadTab(tabName) {
    state.downloadActiveTab = tabName;
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    const ps = (typeof state !== "undefined" && state.downloadPatchset) || (c && c.patch_set) || 1;
    const cmds = computePgDownloadCommands(c, state.downloadScheme, ps);

    const tabs = document.querySelectorAll(".pg-download-tab");
    tabs.forEach(t => {
      t.classList.toggle("active", t.getAttribute("data-tab") === tabName);
    });

    const singleBox = document.getElementById("pgDownloadSingleCommandBox");
    const allBox = document.getElementById("pgDownloadAllCommandsBox");
    const hintEl = document.getElementById("pgDownloadHint");
    const inp = document.getElementById("pgDownloadCommandInput");
    const copyBtn = document.getElementById("btnCopyDownloadCommand");

    if (tabName === "all") {
      if (singleBox) singleBox.style.display = "none";
      if (allBox) {
        allBox.style.display = "flex";
        allBox.innerHTML = renderPgDownloadAllCommandsHtml(c, state.downloadScheme);
      }
      if (hintEl) {
        hintEl.innerHTML = `<span>💡 Review and copy any download or patch command for <code>ag/${num}</code> (PS ${ps}).</span>`;
      }
      return;
    }

    if (allBox) allBox.style.display = "none";
    if (singleBox) singleBox.style.display = "flex";

    const cmd = cmds[tabName] || cmds.repo || "";
    if (inp) {
      inp.value = cmd;
    }
    if (copyBtn) {
      copyBtn.textContent = "📋 Copy";
      copyBtn.classList.remove("is-copied");
    }

    const hintMap = {
      repo: "💡 Downloads and applies this patchset in your Repo workspace checkout.",
      checkout: "💡 Fetches and checks out this change directly to FETCH_HEAD" + (cmds.useBranch ? ` (branch: ${cmds.branchName})` : "") + ".",
      "cherry-pick": "💡 Applies this commit on top of your current branch as a cherry-pick.",
      "format-patch": "💡 Fetches the patch and formats as a git mail patch.",
      pull: "💡 Fetches and merges this change directly into your current branch.",
      patch: "💡 Fetches and applies the raw patch directly with git apply.",
    };
    if (hintEl) {
      hintEl.innerHTML = `<span>${hintMap[tabName] || "💡 Run this command in your local checkout to pull and test this change."}</span>`;
    }
  }

  function copyPgDownloadCommand() {
    const inp = document.getElementById("pgDownloadCommandInput");
    const copyBtn = document.getElementById("btnCopyDownloadCommand");
    if (!inp) return;
    const text = inp.value || "";
    if (!text) return;

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(text).catch(() => {});
    } else {
      inp.select();
      try { document.execCommand("copy"); } catch (_e) {}
    }

    if (copyBtn) {
      copyBtn.textContent = "✓ Copied";
      copyBtn.classList.add("is-copied");
      setTimeout(() => {
        if (copyBtn) {
          copyBtn.textContent = "📋 Copy";
          copyBtn.classList.remove("is-copied");
        }
      }, 2000);
    }
    showToast("Copied command to clipboard!");
  }

  function copyPgSpecificDownloadCommand(cmdKey) {
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    const ps = (typeof state !== "undefined" && state.downloadPatchset) || (c && c.patch_set) || 1;
    const cmds = computePgDownloadCommands(c, state.downloadScheme, ps);
    const cmd = cmds[cmdKey] || "";
    if (!cmd) return;

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(cmd).catch(() => {});
    }

    const btn = document.getElementById(`btnCopyCmdRow-${cmdKey}`);
    if (btn) {
      btn.textContent = "✓ Copied";
      btn.classList.add("is-copied");
      setTimeout(() => {
        if (btn) {
          btn.textContent = "📋 Copy";
          btn.classList.remove("is-copied");
        }
      }, 2000);
    }
    showToast(`Copied ${cmdKey} command to clipboard!`);
  }

  function copyPgDownloadRef() {
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    const ps = (typeof state !== "undefined" && state.downloadPatchset) || (c && c.patch_set) || 1;
    const cmds = computePgDownloadCommands(c, state.downloadScheme, ps);
    const ref = cmds.ref || "";
    if (!ref) return;

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(ref).catch(() => {});
    }
    showToast(`Copied ref: ${ref}`);
  }

  function copyPgPatchUrl() {
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    const ps = Number((typeof state !== "undefined" && state.downloadPatchset) || c.patch_set || 1);
    const rawHost = c.host || (c.url ? (c.url.split("/c/")[0] || "") : "");
    const host = rawHost ? rawHost.replace(/\/+$/, "") : "https://android-review.googlesource.com";
    const patchUrl = `${host}/changes/${num}/revisions/${ps}/patch`;

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(patchUrl).catch(() => {});
    }
    showToast(`Copied patch URL to clipboard!`);
  }

  function copyPgWorkspaceCommand() {
    const num = state.downloadDialogCl || state.selectedClNumber;
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    const ps = Number((typeof state !== "undefined" && state.downloadPatchset) || c.patch_set || 1);
    const proj = c.project || "platform/hardware/google/aemu";
    const cmd = (typeof state !== "undefined" && state.downloadRepoCreateBranch)
      ? `repo download -c ${proj} ${num}/${ps}`
      : `repo download ${proj} ${num}/${ps}`;

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(cmd).catch(() => {});
    }
    showToast(`Copied local repo command: ${cmd}`);
  }

  function downloadPgPatchFile(clNum) {
    const num = Number(clNum || state.downloadDialogCl || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return false;
    const ps = Number((typeof state !== "undefined" && state.downloadPatchset) || c.patch_set || 1);
    const fileName = `change-${num}-ps${ps}.patch`;
    const bundle = state.diffsByCl && state.diffsByCl[num];

    let patchText = "";
    if (bundle && bundle.raw_diff) {
      patchText = bundle.raw_diff;
    } else if (bundle && Array.isArray(bundle.files) && bundle.files.length > 0) {
      const parts = [];
      parts.push(`From: ${c.owner_name || "Author"} <${c.owner_email || "author@example.com"}>`);
      parts.push(`Date: ${c.updated || new Date().toISOString()}`);
      parts.push(`Subject: ${c.subject || ""}\n`);
      if (bundle.commit_message) {
        parts.push(bundle.commit_message + "\n");
      }
      bundle.files.forEach(f => {
        if (f.file === "/COMMIT_MSG" || f.file === "/MERGE_LIST") return;
        parts.push(`diff --git a/${f.file} b/${f.file}`);
        parts.push(`--- a/${f.file}`);
        parts.push(`+++ b/${f.file}`);
        if (Array.isArray(f.lines)) {
          f.lines.forEach(l => {
            const prefix = l.type === "add" ? "+" : l.type === "del" ? "-" : " ";
            parts.push(prefix + (l.text || ""));
          });
        }
      });
      patchText = parts.join("\n") + "\n";
    }

    if (patchText && typeof Blob !== "undefined" && typeof document !== "undefined") {
      try {
        const blob = new Blob([patchText], { type: "text/x-diff;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(`Downloaded patch file: ${fileName} ✓`);
        return true;
      } catch (_e) {}
    }

    // Fallback to Gerrit URL
    const rawHost = c.host || (c.url ? (c.url.split("/c/")[0] || "") : "");
    const host = rawHost ? rawHost.replace(/\/+$/, "") : "https://android-review.googlesource.com";
    const patchUrl = `${host}/changes/${num}/revisions/${ps}/patch?zip`;
    if (typeof window !== "undefined" && window.open) {
      window.open(patchUrl, "_blank");
    }
    showToast(`Opened patch download in browser: ${fileName} ✓`);
    return true;
  }

  function getPgChangeDescendants(clNum) {
    const num = Number(clNum || (typeof state !== "undefined" ? state.selectedClNumber : 0));
    if (!num) return [];
    if (typeof findPgStackForClOrId === "function") {
      const st = findPgStackForClOrId(num);
      if (st && Array.isArray(st.commits) && st.commits.length > 1) {
        const idx = st.commits.findIndex(item => Number(item && item.number) === num);
        if (idx >= 0 && idx < st.commits.length - 1) {
          return st.commits.slice(idx + 1).filter(item => item && Number(item.number) !== num);
        }
      }
    }
    return [];
  }

  function getPgRebaseCandidateBases(clNum) {
    const num = Number(clNum || (typeof state !== "undefined" ? state.selectedClNumber : 0));
    const candidates = [];
    const seen = new Set();
    if (num) seen.add(num);

    const c = (typeof resolvePgChangeForAction === "function") ? resolvePgChangeForAction(num) : null;

    // 1. Stack / relation chain peers
    if (typeof findPgStackForClOrId === "function") {
      const st = findPgStackForClOrId(num);
      if (st && Array.isArray(st.commits)) {
        for (const peer of st.commits) {
          const pNum = Number(peer && peer.number);
          if (pNum && !seen.has(pNum)) {
            seen.add(pNum);
            candidates.push({
              cl: pNum,
              subject: peer.subject || "",
              tag: "Stack",
              branch: peer.branch || "",
            });
          }
        }
      }
    }

    // 2. Same topic peers
    if (c && typeof getPgTopicPeers === "function") {
      const topicPeers = getPgTopicPeers(c);
      if (Array.isArray(topicPeers)) {
        for (const peer of topicPeers) {
          const pNum = Number(peer && peer.number);
          if (pNum && !seen.has(pNum)) {
            seen.add(pNum);
            candidates.push({
              cl: pNum,
              subject: peer.subject || "",
              tag: "Topic",
              branch: peer.branch || "",
            });
          }
        }
      }
    }

    // 3. Recent open changes from queue
    if (typeof state !== "undefined" && Array.isArray(state.changes)) {
      for (const ch of state.changes) {
        const pNum = Number(ch && ch.number);
        if (pNum && !seen.has(pNum) && candidates.length < 12) {
          const st = String(ch.status || "").toUpperCase();
          if (st !== "MERGED" && st !== "ABANDONED") {
            seen.add(pNum);
            candidates.push({
              cl: pNum,
              subject: ch.subject || "",
              tag: "Queue",
              branch: ch.branch || "",
            });
          }
        }
      }
    }

    return candidates;
  }

  function renderPgRebaseSuggestions(clNum, filterText = "") {
    if (typeof document === "undefined") return;
    const el = document.getElementById("pgRebaseSuggestions");
    if (!el) return;
    const candidates = getPgRebaseCandidateBases(clNum);
    const customInp = document.getElementById("pgRebaseCustomBaseInput");
    const currentVal = customInp ? customInp.value.trim().toLowerCase() : "";
    const query = String(filterText || currentVal).toLowerCase();

    const filtered = candidates.filter(cand => {
      if (!query) return true;
      return String(cand.cl).includes(query) || (cand.subject && cand.subject.toLowerCase().includes(query));
    });

    if (filtered.length === 0) {
      el.innerHTML = '<span style="font-size:11px;color:var(--g-text-secondary,#9aa0a6);font-style:italic;">No suggested changes found</span>';
      return;
    }

    const html = filtered.slice(0, 8).map(cand => {
      const isSelected = customInp && customInp.value.trim() === String(cand.cl);
      const selCls = isSelected ? "is-selected" : "";
      const subj = cand.subject ? cand.subject.slice(0, 36) : "";
      return `<button type="button" class="pg-rebase-suggestion-chip ${selCls}" data-cl="${cand.cl}" onclick="selectPgRebaseCustomBase(${cand.cl}, '${escapeHtml(cand.subject || "")}')" title="ag/${cand.cl}: ${escapeHtml(cand.subject || "")}">
        <span class="pg-rebase-suggestion-cl">ag/${cand.cl}</span>
        <span class="pg-rebase-suggestion-tag">${escapeHtml(cand.tag)}</span>
        <span>${escapeHtml(subj)}</span>
      </button>`;
    }).join("");

    el.innerHTML = html;
  }

  function selectPgRebaseCustomBase(baseVal, label) {
    if (typeof document === "undefined") return;
    const customInp = document.getElementById("pgRebaseCustomBaseInput");
    const customRadio = document.querySelector('input[name="pgRebaseTargetType"][value="custom"]');
    const suggestionsWrap = document.getElementById("pgRebaseSuggestionsWrap");
    if (customRadio) {
      customRadio.checked = true;
    }
    if (customInp) {
      customInp.disabled = false;
      customInp.value = String(baseVal);
      if (typeof customInp.focus === "function") customInp.focus();
    }
    if (suggestionsWrap) {
      suggestionsWrap.style.display = "block";
    }
    const chips = document.querySelectorAll("#pgRebaseSuggestions .pg-rebase-suggestion-chip");
    chips.forEach(chip => {
      if (chip.getAttribute("data-cl") === String(baseVal)) {
        chip.classList.add("is-selected");
      } else {
        chip.classList.remove("is-selected");
      }
    });
  }

  function filterPgRebaseSuggestions(filterText) {
    const num = (typeof state !== "undefined" && (state.rebaseDialogCl || state.selectedClNumber)) || 0;
    renderPgRebaseSuggestions(num, filterText);
  }

  function onPgRebaseCustomBaseInput(val) {
    filterPgRebaseSuggestions(val);
  }

  function openPgRebaseDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.rebaseDialogOpen = true;
    state.rebaseDialogCl = num;

    if (typeof document === "undefined") return;

    const backdrop = document.getElementById("pgRebaseBackdrop");
    const subEl = document.getElementById("pgRebaseSubtitle");
    const branchEl = document.getElementById("pgRebaseBranchName");
    const customInp = document.getElementById("pgRebaseCustomBaseInput");
    const suggestionsWrap = document.getElementById("pgRebaseSuggestionsWrap");
    const parentRadio = document.querySelector('input[name="pgRebaseTargetType"][value="parent"]');
    const chainOption = document.getElementById("pgRebaseChainParentOption");
    const chainLabel = document.getElementById("pgRebaseChainParentLabel");
    const chainRadio = document.querySelector('input[name="pgRebaseTargetType"][value="chain_parent"]');
    const conflictBanner = document.getElementById("pgRebaseConflictBanner");
    const outdatedBanner = document.getElementById("pgRebaseOutdatedBanner");
    const outdatedDesc = document.getElementById("pgRebaseOutdatedDesc");
    const parentMergedBanner = document.getElementById("pgRebaseParentMergedBanner");
    const parentMergedDesc = document.getElementById("pgRebaseParentMergedDesc");
    const allowConflictsCb = document.getElementById("pgRebaseAllowConflicts");
    const chainDescRow = document.getElementById("pgRebaseChainDescendantsRow");
    const chainDescLabel = document.getElementById("pgRebaseChainDescendantsLabel");
    const chainDescCb = document.getElementById("pgRebaseChainDescendants");

    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""} (PS ${c.patch_set || 1})`;
    if (branchEl) branchEl.textContent = c.branch || "main";

    // Resolve rebase status using getPgChangeRebaseStatus if available
    const rebaseStatusFn = (typeof getPgChangeRebaseStatus === "function")
      ? getPgChangeRebaseStatus
      : ((typeof window !== "undefined" && typeof window.getPgChangeRebaseStatus === "function") ? window.getPgChangeRebaseStatus : null);
    const rebaseStatus = rebaseStatusFn
      ? rebaseStatusFn(c)
      : { hasConflict: false, isOutdated: false, isParentMerged: false, parentCommit: null, parentClNumber: null, parentSubject: "", parentCurrentPs: 1 };

    // Resolve parent commit
    state._rebaseChainParentNum = null;
    let parentCommit = rebaseStatus.parentCommit;
    if (!parentCommit && rebaseStatus.parentClNumber) {
      parentCommit = {
        number: rebaseStatus.parentClNumber,
        subject: rebaseStatus.parentSubject,
        patch_set: rebaseStatus.parentCurrentPs,
      };
    }
    if (!parentCommit && typeof findPgStackForClOrId === "function") {
      const st = findPgStackForClOrId(num, state.selectedStackId);
      if (st && Array.isArray(st.commits) && st.commits.length > 1) {
        const idx = st.commits.findIndex(item => Number(item && item.number) === num);
        if (idx > 0 && st.commits[idx - 1]) {
          parentCommit = st.commits[idx - 1];
        }
      }
    }
    if (parentCommit && parentCommit.number) {
      state._rebaseChainParentNum = Number(parentCommit.number);
    }

    if (chainOption && chainLabel) {
      if (parentCommit) {
        chainOption.style.display = "flex";
        const psLabel = parentCommit.patch_set ? ` (PS ${parentCommit.patch_set})` : "";
        const outdatedTag = rebaseStatus.isOutdated ? " ⚠️ outdated" : "";
        chainLabel.textContent = `ag/${parentCommit.number}: ${(parentCommit.subject || "").slice(0, 44)}${psLabel}${outdatedTag}`;
        chainLabel.title = `ag/${parentCommit.number}: ${parentCommit.subject || ""}${psLabel}`;
      } else {
        chainOption.style.display = "none";
      }
    }

    // Default target:
    // If parent merged -> parent branch
    // Else if parent commit in stack -> chain_parent
    // Else -> parent branch
    if (rebaseStatus.isParentMerged && parentRadio) {
      parentRadio.checked = true;
    } else if (parentCommit && chainRadio) {
      chainRadio.checked = true;
    } else if (parentRadio) {
      parentRadio.checked = true;
    }

    if (customInp) {
      customInp.value = "";
      customInp.disabled = true;
    }
    if (suggestionsWrap) {
      suggestionsWrap.style.display = "none";
      renderPgRebaseSuggestions(num);
    }

    // Outdated base banner
    if (outdatedBanner) {
      if (rebaseStatus.isOutdated) {
        outdatedBanner.style.display = "flex";
        if (outdatedDesc) {
          outdatedDesc.textContent = `Parent change ag/${rebaseStatus.parentClNumber} has updated patchset PS ${rebaseStatus.parentCurrentPs}. Rebasing will update this change onto PS ${rebaseStatus.parentCurrentPs}.`;
        }
      } else {
        outdatedBanner.style.display = "none";
      }
    }

    // Parent merged banner
    if (parentMergedBanner) {
      if (rebaseStatus.isParentMerged) {
        parentMergedBanner.style.display = "flex";
        if (parentMergedDesc) {
          parentMergedDesc.textContent = `Parent change ag/${rebaseStatus.parentClNumber} was merged into ${c.branch || "main"}. Recommended: Rebase on parent branch.`;
        }
      } else {
        parentMergedBanner.style.display = "none";
      }
    }

    // Detect merge conflict
    const isConflict = Boolean(rebaseStatus.hasConflict) || (c.mergeable === false) || (String(c.status || "").toLowerCase() === "conflict");
    if (conflictBanner) {
      conflictBanner.style.display = isConflict ? "flex" : "none";
    }
    if (allowConflictsCb) {
      allowConflictsCb.checked = isConflict;
    }

    // Descendants & Rebase Whole Chain
    const descendants = getPgChangeDescendants(num);
    if (chainDescRow) {
      if (descendants.length > 0) {
        chainDescRow.style.display = "flex";
        if (chainDescLabel) {
          chainDescLabel.textContent = `Rebase whole chain (${descendants.length} descendant change${descendants.length > 1 ? "s" : ""})`;
        }
        if (chainDescCb) {
          chainDescCb.checked = false;
        }
      } else {
        chainDescRow.style.display = "none";
        if (chainDescCb) {
          chainDescCb.checked = false;
        }
      }
    }

    const rebaseBtn = document.getElementById("btnConfirmRebase");
    if (rebaseBtn) {
      rebaseBtn.disabled = false;
      rebaseBtn.textContent = "Rebase";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
  }

  function closePgRebaseDialog() {
    state.rebaseDialogOpen = false;
    state.rebaseDialogCl = null;
    const backdrop = typeof document !== "undefined" ? document.getElementById("pgRebaseBackdrop") : null;
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgRebaseBackdropClick(e) {
    if (e.target && e.target.id === "pgRebaseBackdrop") {
      closePgRebaseDialog();
    }
  }

  function onPgRebaseTypeChange(val) {
    if (typeof document === "undefined") return;
    const customInp = document.getElementById("pgRebaseCustomBaseInput");
    const suggestionsWrap = document.getElementById("pgRebaseSuggestionsWrap");
    if (val === "custom") {
      if (customInp) {
        customInp.disabled = false;
        if (typeof customInp.focus === "function") customInp.focus();
      }
      if (suggestionsWrap) {
        suggestionsWrap.style.display = "block";
      }
    } else {
      if (customInp) {
        customInp.disabled = true;
      }
      if (suggestionsWrap) {
        suggestionsWrap.style.display = "none";
      }
    }
  }

  async function executePgRebase() {
    const num = state.rebaseDialogCl || state.selectedClNumber;
    if (!num) return;
    const selectedRadio = typeof document !== "undefined" ? document.querySelector('input[name="pgRebaseTargetType"]:checked') : null;
    const targetType = selectedRadio ? selectedRadio.value : "parent";
    const customInp = typeof document !== "undefined" ? document.getElementById("pgRebaseCustomBaseInput") : null;
    const allowConflictsCb = typeof document !== "undefined" ? document.getElementById("pgRebaseAllowConflicts") : null;
    const chainDescCb = typeof document !== "undefined" ? document.getElementById("pgRebaseChainDescendants") : null;

    let baseVal = null;
    if (targetType === "chain_parent" && state._rebaseChainParentNum) {
      baseVal = String(state._rebaseChainParentNum);
    } else if (targetType === "custom") {
      baseVal = (customInp && customInp.value.trim()) || null;
      if (!baseVal) {
        showToast("Please specify a base change or commit SHA for custom rebase.");
        if (customInp && typeof customInp.focus === "function") customInp.focus();
        return;
      }
    }
    const allowConflicts = Boolean(allowConflictsCb && allowConflictsCb.checked);
    const rebaseChain = Boolean(chainDescCb && chainDescCb.checked);

    const rebaseBtn = typeof document !== "undefined" ? document.getElementById("btnConfirmRebase") : null;
    if (rebaseBtn) {
      rebaseBtn.disabled = true;
      rebaseBtn.textContent = "Rebasing…";
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/rebase", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          base: baseVal,
          allow_conflicts: allowConflicts,
          rebase_chain: rebaseChain,
          viewer: state.viewer,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      closePgRebaseDialog();
      const conflictMsg = allowConflicts ? " (conflicts allowed)" : "";
      const chainMsg = rebaseChain ? " and descendants" : "";
      showToast(`✓ ag/${num}${chainMsg} rebased successfully${conflictMsg}! Refreshing…`);
      const c = resolvePgChangeForAction(num);
      if (c && data.change) {
        Object.assign(c, data.change);
      }
      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
      if (typeof renderDiffPane === "function") {
        renderDiffPane();
      }
      if (typeof refreshQueueAsync === "function") {
        await refreshQueueAsync();
      }
    } catch (err) {
      if (rebaseBtn) {
        rebaseBtn.disabled = false;
        rebaseBtn.textContent = "Rebase";
      }
      showToast(`Rebase failed: ${err.message || err}`);
    }
  }

  function openPgAbandonDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.abandonDialogOpen = true;
    state.abandonDialogCl = num;

    const backdrop = document.getElementById("pgAbandonBackdrop");
    const subEl = document.getElementById("pgAbandonSubtitle");
    const reasonInp = document.getElementById("pgAbandonReasonInput");

    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""}`;
    if (reasonInp) {
      reasonInp.value = "";
    }

    const abandonBtn = document.getElementById("btnConfirmAbandon");
    if (abandonBtn) {
      abandonBtn.disabled = false;
      abandonBtn.textContent = "Abandon Change";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (reasonInp) reasonInp.focus();
    }, 50);
  }

  function closePgAbandonDialog() {
    state.abandonDialogOpen = false;
    state.abandonDialogCl = null;
    const backdrop = document.getElementById("pgAbandonBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgAbandonBackdropClick(e) {
    if (e.target && e.target.id === "pgAbandonBackdrop") {
      closePgAbandonDialog();
    }
  }

  async function executePgAbandon() {
    const num = state.abandonDialogCl || state.selectedClNumber;
    if (!num) return;
    const reasonInp = document.getElementById("pgAbandonReasonInput");
    const reason = reasonInp ? reasonInp.value.trim() : "";

    const abandonBtn = document.getElementById("btnConfirmAbandon");
    if (abandonBtn) {
      abandonBtn.disabled = true;
      abandonBtn.textContent = "Abandoning…";
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/abandon", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          message: reason,
          viewer: state.viewer,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      closePgAbandonDialog();
      showToast(`ag/${num} abandoned`);
      const c = resolvePgChangeForAction(num);
      if (c) {
        c.status = "ABANDONED";
        c.is_abandoned = true;
      }
      renderDiffPane();
      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
    } catch (err) {
      if (abandonBtn) {
        abandonBtn.disabled = false;
        abandonBtn.textContent = "Abandon Change";
      }
      showToast(`Abandon failed: ${err.message || err}`);
    }
  }

  function openPgRestoreDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.restoreDialogOpen = true;
    state.restoreDialogCl = num;

    const backdrop = document.getElementById("pgRestoreBackdrop");
    const subEl = document.getElementById("pgRestoreSubtitle");
    const reasonInp = document.getElementById("pgRestoreReasonInput");

    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""}`;
    if (reasonInp) {
      reasonInp.value = "";
    }

    const restoreBtn = document.getElementById("btnConfirmRestore");
    if (restoreBtn) {
      restoreBtn.disabled = false;
      restoreBtn.textContent = "Restore Change";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (reasonInp) reasonInp.focus();
    }, 50);
  }

  function closePgRestoreDialog() {
    state.restoreDialogOpen = false;
    state.restoreDialogCl = null;
    const backdrop = document.getElementById("pgRestoreBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgRestoreBackdropClick(e) {
    if (e.target && e.target.id === "pgRestoreBackdrop") {
      closePgRestoreDialog();
    }
  }

  async function executePgRestore() {
    const num = state.restoreDialogCl || state.selectedClNumber;
    if (!num) return;
    const reasonInp = document.getElementById("pgRestoreReasonInput");
    const reason = reasonInp ? reasonInp.value.trim() : "";

    const restoreBtn = document.getElementById("btnConfirmRestore");
    if (restoreBtn) {
      restoreBtn.disabled = true;
      restoreBtn.textContent = "Restoring…";
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          message: reason,
          viewer: state.viewer,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      closePgRestoreDialog();
      showToast(`ag/${num} restored`);
      const c = resolvePgChangeForAction(num);
      if (c) {
        c.status = "NEW";
        c.is_abandoned = false;
      }
      renderDiffPane();
      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
    } catch (err) {
      if (restoreBtn) {
        restoreBtn.disabled = false;
        restoreBtn.textContent = "Restore Change";
      }
      showToast(`Restore failed: ${err.message || err}`);
    }
  }

  function openPgRevertDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.revertDialogOpen = true;
    state.revertDialogCl = num;

    const backdrop = document.getElementById("pgRevertBackdrop");
    const subEl = document.getElementById("pgRevertSubtitle");
    const msgInp = document.getElementById("pgRevertMessageInput");
    const topicInp = document.getElementById("pgRevertTopicInput");

    const subject = (c.subject || "").trim();
    if (subEl) subEl.textContent = `ag/${num}: ${subject}`;

    const revSha = c.current_revision || (c.revisions && Object.keys(c.revisions)[0]) || "";
    const commitShaLine = revSha ? `This reverts commit ${revSha}.\n\n` : "";
    const defaultMsg = `Revert "${subject}"\n\n${commitShaLine}Reason for revert: `;

    if (msgInp) {
      msgInp.value = defaultMsg;
    }
    if (topicInp) {
      topicInp.value = (c.topic || "").trim();
    }

    const revertBtn = document.getElementById("btnConfirmRevert");
    if (revertBtn) {
      revertBtn.disabled = false;
      revertBtn.textContent = "Revert Change";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (msgInp) {
        msgInp.focus();
        msgInp.setSelectionRange(msgInp.value.length, msgInp.value.length);
      }
    }, 50);
  }

  function closePgRevertDialog() {
    state.revertDialogOpen = false;
    state.revertDialogCl = null;
    const backdrop = document.getElementById("pgRevertBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgRevertBackdropClick(e) {
    if (e.target && e.target.id === "pgRevertBackdrop") {
      closePgRevertDialog();
    }
  }

  async function executePgRevert() {
    const num = state.revertDialogCl || state.selectedClNumber;
    if (!num) return;
    const msgInp = document.getElementById("pgRevertMessageInput");
    const topicInp = document.getElementById("pgRevertTopicInput");
    const message = msgInp ? msgInp.value.trim() : "";
    const topic = topicInp ? topicInp.value.trim() : "";

    if (!message) {
      showToast("Please provide a revert commit message / reason.");
      if (msgInp) msgInp.focus();
      return;
    }

    const revertBtn = document.getElementById("btnConfirmRevert");
    if (revertBtn) {
      revertBtn.disabled = true;
      revertBtn.textContent = "Reverting…";
    }

    try {
      const payload = {
        cl: num,
        message,
        viewer: state.viewer,
      };
      if (topic) {
        payload.topic = topic;
      }
      const resp = await sidecarFetch("/api/gerrit/revert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      closePgRevertDialog();
      const newCl = data.revert_cl || data.new_cl || (data.response && (data.response._number || data.response.change_id));
      if (newCl) {
        showToast(`Revert change ag/${newCl} created`);
      } else {
        showToast(`ag/${num} reverted`);
      }
      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
      if (typeof refreshQueueAsync === "function") {
        await refreshQueueAsync();
      }
      if (newCl && typeof selectChange === "function") {
        selectChange(Number(newCl));
      }
    } catch (err) {
      if (revertBtn) {
        revertBtn.disabled = false;
        revertBtn.textContent = "Revert Change";
      }
      showToast(`Revert failed: ${err.message || err}`);
    }
  }

  function openPgSetWipDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.setWipDialogOpen = true;
    state.setWipDialogCl = num;

    const backdrop = document.getElementById("pgSetWipBackdrop");
    const subEl = document.getElementById("pgSetWipSubtitle");
    const reasonInp = document.getElementById("pgSetWipReasonInput");

    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""}`;
    if (reasonInp) {
      reasonInp.value = "";
    }

    const confirmBtn = document.getElementById("btnConfirmSetWip");
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Mark as WIP";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (reasonInp) reasonInp.focus();
    }, 50);
  }

  function closePgSetWipDialog() {
    state.setWipDialogOpen = false;
    state.setWipDialogCl = null;
    const backdrop = document.getElementById("pgSetWipBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgSetWipBackdropClick(e) {
    if (e.target && e.target.id === "pgSetWipBackdrop") {
      closePgSetWipDialog();
    }
  }

  async function executePgSetWip() {
    const num = state.setWipDialogCl || state.selectedClNumber;
    if (!num) return;
    const reasonInp = document.getElementById("pgSetWipReasonInput");
    const reason = reasonInp ? reasonInp.value.trim() : "";

    const confirmBtn = document.getElementById("btnConfirmSetWip");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Marking WIP…";
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/set-wip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          message: reason,
          viewer: state.viewer,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      closePgSetWipDialog();
      showToast(`ag/${num} marked as Work in Progress (Alt+W)`);
      const c = resolvePgChangeForAction(num);
      if (c) {
        c.work_in_progress = true;
      }
      renderDiffPane();
      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
      if (typeof syncActiveActionButtons === "function") {
        syncActiveActionButtons(getActiveCommitsForDiff(), false);
      }
    } catch (err) {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Mark as WIP";
      }
      showToast(`Mark WIP failed: ${err.message || err}`);
    }
  }

  function openPgSetReadyDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.setReadyDialogOpen = true;
    state.setReadyDialogCl = num;

    const backdrop = document.getElementById("pgSetReadyBackdrop");
    const subEl = document.getElementById("pgSetReadySubtitle");
    const reasonInp = document.getElementById("pgSetReadyReasonInput");

    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""}`;
    if (reasonInp) {
      reasonInp.value = "";
    }

    const confirmBtn = document.getElementById("btnConfirmSetReady");
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Mark as Active";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (reasonInp) reasonInp.focus();
    }, 50);
  }

  function closePgSetReadyDialog() {
    state.setReadyDialogOpen = false;
    state.setReadyDialogCl = null;
    const backdrop = document.getElementById("pgSetReadyBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgSetReadyBackdropClick(e) {
    if (e.target && e.target.id === "pgSetReadyBackdrop") {
      closePgSetReadyDialog();
    }
  }

  async function executePgSetReady() {
    const num = state.setReadyDialogCl || state.selectedClNumber;
    if (!num) return;
    const reasonInp = document.getElementById("pgSetReadyReasonInput");
    const reason = reasonInp ? reasonInp.value.trim() : "";

    const confirmBtn = document.getElementById("btnConfirmSetReady");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Marking Active…";
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/set-ready", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          message: reason,
          viewer: state.viewer,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      closePgSetReadyDialog();
      showToast(`ag/${num} marked as Ready for Review (active) (Alt+W)`);
      const c = resolvePgChangeForAction(num);
      if (c) {
        c.work_in_progress = false;
      }
      renderDiffPane();
      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
      if (typeof syncActiveActionButtons === "function") {
        syncActiveActionButtons(getActiveCommitsForDiff(), false);
      }
    } catch (err) {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Mark as Active";
      }
      showToast(`Mark Active failed: ${err.message || err}`);
    }
  }

  function togglePgWorkInProgress(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number));
    if (!num) return;
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    if (c.work_in_progress) {
      openPgSetReadyDialog(num);
    } else {
      openPgSetWipDialog(num);
    }
  }

  const PG_DEFAULT_CHERRY_PICK_BRANCHES = [
    "goog/studio-main",
    "master",
    "main",
    "emu-master-dev",
    "sys-img-dev",
    "mirror-goog-studio-main"
  ];

  function getPgRecentCherryPickBranches() {
    try {
      const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return [];
      const raw = ls.getItem("pg_recent_cherrypick_branches");
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter(b => typeof b === "string" && b.trim()) : [];
    } catch (_) {
      return [];
    }
  }

  function savePgRecentCherryPickBranch(branchName) {
    const br = String(branchName || "").trim();
    if (!br) return;
    try {
      const ls = (typeof window !== "undefined" && window.localStorage) ? window.localStorage : (typeof localStorage !== "undefined" ? localStorage : null);
      if (!ls) return;
      const recent = getPgRecentCherryPickBranches().filter(b => b !== br);
      recent.unshift(br);
      ls.setItem("pg_recent_cherrypick_branches", JSON.stringify(recent.slice(0, 8)));
    } catch (_) {}
  }

  function getPgCherryPickCandidateBranches(clNum) {
    const num = Number(clNum || (typeof state !== "undefined" ? state.selectedClNumber : 0));
    const c = (typeof resolvePgChangeForAction === "function") ? resolvePgChangeForAction(num) : null;
    const curBranch = c && c.branch ? String(c.branch).trim() : "";
    const curProj = c && c.project ? String(c.project).trim() : "";

    const candidates = [];
    const seen = new Set();

    // 1. Recent destination branches from localStorage
    const recents = getPgRecentCherryPickBranches();
    for (const b of recents) {
      const clean = String(b).trim();
      if (clean && !seen.has(clean)) {
        seen.add(clean);
        candidates.push({
          branch: clean,
          tag: "Recent",
          isCurrent: clean === curBranch,
        });
      }
    }

    // 2. Project branches from open changes / stacks sharing project
    if (typeof state !== "undefined" && Array.isArray(state.changes)) {
      for (const ch of state.changes) {
        const br = ch && ch.branch ? String(ch.branch).trim() : "";
        if (br && !seen.has(br)) {
          const sameProj = !curProj || !ch.project || ch.project === curProj;
          seen.add(br);
          candidates.push({
            branch: br,
            tag: sameProj ? "Project" : "Queue",
            isCurrent: br === curBranch,
          });
        }
      }
    }

    // 3. Default standard branches
    for (const d of PG_DEFAULT_CHERRY_PICK_BRANCHES) {
      if (!seen.has(d)) {
        seen.add(d);
        candidates.push({
          branch: d,
          tag: "Default",
          isCurrent: d === curBranch,
        });
      }
    }

    return candidates;
  }

  function getPgCherryPickCandidateBases(clNum) {
    const num = Number(clNum || (typeof state !== "undefined" ? state.selectedClNumber : 0));
    if (typeof getPgRebaseCandidateBases === "function") {
      return getPgRebaseCandidateBases(num);
    }
    return [];
  }

  function renderPgCherryPickBranchSuggestions(clNum, filterText = "") {
    if (typeof document === "undefined") return;
    const chipsEl = document.getElementById("pgCherryPickBranchChips");
    const dropdownEl = document.getElementById("pgCherryPickBranchDropdown");
    const branchInp = document.getElementById("pgCherryPickBranchInput");
    const currentVal = branchInp ? branchInp.value.trim().toLowerCase() : "";
    const query = String(filterText || currentVal).toLowerCase();

    const candidates = getPgCherryPickCandidateBranches(clNum);
    const filtered = candidates.filter(cand => {
      if (!query) return true;
      return cand.branch.toLowerCase().includes(query);
    });

    if (chipsEl) {
      if (filtered.length === 0) {
        chipsEl.innerHTML = '<span style="font-size:11px;color:var(--g-text-secondary,#9aa0a6);font-style:italic;">No suggested branches</span>';
      } else {
        const chipsHtml = filtered.slice(0, 6).map(cand => {
          const isSelected = branchInp && branchInp.value.trim() === cand.branch;
          const selCls = isSelected ? "is-selected" : "";
          const tagCls = `tag-${cand.tag.toLowerCase()}`;
          return `<button type="button" class="pg-branch-chip ${selCls}" data-branch="${escapeHtml(cand.branch)}" onclick="setPgCherryPickBranch('${escapeHtml(cand.branch)}')">
            <span>${escapeHtml(cand.branch)}</span>
            <span class="pg-branch-chip-tag ${tagCls}">${escapeHtml(cand.tag)}</span>
          </button>`;
        }).join("");
        chipsEl.innerHTML = chipsHtml;
      }
    }

    if (dropdownEl) {
      if (!query && filtered.length === 0) {
        dropdownEl.style.display = "none";
        return;
      }
      if (filtered.length === 0) {
        dropdownEl.innerHTML = `<div class="pg-branch-dropdown-empty">No matching branches (press Enter to use &quot;${escapeHtml(filterText)}&quot;)</div>`;
        dropdownEl.style.display = "block";
        return;
      }
      const itemsHtml = filtered.slice(0, 10).map((cand, idx) => {
        const isSelected = branchInp && branchInp.value.trim() === cand.branch;
        const curIdx = (typeof state !== "undefined" && typeof state.cherryPickDropdownIndex === "number") ? state.cherryPickDropdownIndex : -1;
        const selCls = (isSelected || idx === curIdx) ? "is-active" : "";
        const tagCls = `tag-${cand.tag.toLowerCase()}`;
        return `<div class="pg-branch-dropdown-item ${selCls}" data-branch="${escapeHtml(cand.branch)}" onclick="setPgCherryPickBranch('${escapeHtml(cand.branch)}')">
          <span>${escapeHtml(cand.branch)}</span>
          <span class="pg-branch-chip-tag ${tagCls}">${escapeHtml(cand.tag)}</span>
        </div>`;
      }).join("");
      dropdownEl.innerHTML = itemsHtml;
      dropdownEl.style.display = "block";
    }
  }

  function renderPgCherryPickBaseSuggestions(clNum, filterText = "") {
    if (typeof document === "undefined") return;
    const wrap = document.getElementById("pgCherryPickBaseSuggestionsWrap");
    const container = document.getElementById("pgCherryPickBaseSuggestions");
    if (!wrap || !container) return;

    const candidates = getPgCherryPickCandidateBases(clNum);
    if (!candidates || candidates.length === 0) {
      wrap.style.display = "none";
      return;
    }

    const baseInp = document.getElementById("pgCherryPickBaseInput");
    const query = String(filterText || (baseInp ? baseInp.value.trim() : "")).toLowerCase();
    const filtered = candidates.filter(cand => {
      if (!query) return true;
      return String(cand.cl).includes(query) || (cand.subject && cand.subject.toLowerCase().includes(query));
    });

    if (filtered.length === 0) {
      wrap.style.display = "none";
      return;
    }

    wrap.style.display = "flex";
    container.innerHTML = filtered.slice(0, 5).map(cand => {
      const isSelected = baseInp && baseInp.value.trim() === String(cand.cl);
      const selCls = isSelected ? "is-selected" : "";
      return `<button type="button" class="pg-branch-chip ${selCls}" data-cl="${cand.cl}" onclick="selectPgCherryPickCustomBase(${cand.cl})" title="ag/${cand.cl}: ${escapeHtml(cand.subject || "")}">
        <span>ag/${cand.cl}</span>
        <span class="pg-branch-chip-tag tag-project">${escapeHtml(cand.tag)}</span>
      </button>`;
    }).join("");
  }

  function selectPgCherryPickCustomBase(baseCl) {
    if (typeof document === "undefined") return;
    const baseInp = document.getElementById("pgCherryPickBaseInput");
    if (baseInp) {
      baseInp.value = String(baseCl);
      baseInp.focus();
    }
    const num = (typeof state !== "undefined" && (state.cherryPickDialogCl || state.selectedClNumber)) || 0;
    renderPgCherryPickBaseSuggestions(num, String(baseCl));
  }

  function onPgCherryPickBaseInput(val) {
    const num = (typeof state !== "undefined" && (state.cherryPickDialogCl || state.selectedClNumber)) || 0;
    renderPgCherryPickBaseSuggestions(num, val);
  }

  function updatePgCherryPickSameBranchWarning(branchName) {
    if (typeof document === "undefined") return;
    const banner = document.getElementById("pgCherryPickSameBranchBanner");
    const textEl = document.getElementById("pgCherryPickSameBranchText");
    if (!banner) return;
    const num = (typeof state !== "undefined" && (state.cherryPickDialogCl || state.selectedClNumber)) || 0;
    const c = (typeof resolvePgChangeForAction === "function") ? resolvePgChangeForAction(num) : null;
    const curBranch = c && c.branch ? String(c.branch).trim() : "";
    const targetBranch = String(branchName || "").trim();

    if (curBranch && targetBranch && curBranch === targetBranch) {
      banner.style.display = "flex";
      if (textEl) {
        textEl.textContent = `Destination branch "${curBranch}" is the same as the change's current branch. Cherry-picking will create an independent duplicate change on this branch.`;
      }
    } else {
      banner.style.display = "none";
    }
  }

  function onPgCherryPickBranchInput(val) {
    if (typeof state !== "undefined") state.cherryPickDropdownIndex = -1;
    const num = (typeof state !== "undefined" && (state.cherryPickDialogCl || state.selectedClNumber)) || 0;
    renderPgCherryPickBranchSuggestions(num, val);
    updatePgCherryPickSameBranchWarning(val);
  }

  function onPgCherryPickBranchFocus() {
    const branchInp = document.getElementById("pgCherryPickBranchInput");
    const val = branchInp ? branchInp.value : "";
    const num = (typeof state !== "undefined" && (state.cherryPickDialogCl || state.selectedClNumber)) || 0;
    renderPgCherryPickBranchSuggestions(num, val);
  }

  function onPgCherryPickBranchKeyDown(e) {
    const dropdownEl = document.getElementById("pgCherryPickBranchDropdown");
    if (!dropdownEl || dropdownEl.style.display === "none") {
      if (e.key === "ArrowDown") {
        const num = (typeof state !== "undefined" && (state.cherryPickDialogCl || state.selectedClNumber)) || 0;
        renderPgCherryPickBranchSuggestions(num, "");
        e.preventDefault();
      }
      return;
    }
    const items = dropdownEl.querySelectorAll(".pg-branch-dropdown-item");
    if (!items || items.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const cur = (typeof state !== "undefined" && typeof state.cherryPickDropdownIndex === "number") ? state.cherryPickDropdownIndex : -1;
      const nextIdx = Math.min(cur + 1, items.length - 1);
      if (typeof state !== "undefined") state.cherryPickDropdownIndex = nextIdx;
      items.forEach((item, idx) => {
        item.classList.toggle("is-active", idx === nextIdx);
        if (idx === nextIdx && typeof item.scrollIntoView === "function") {
          item.scrollIntoView({ block: "nearest" });
        }
      });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const cur = (typeof state !== "undefined" && typeof state.cherryPickDropdownIndex === "number") ? state.cherryPickDropdownIndex : 0;
      const prevIdx = Math.max(cur - 1, 0);
      if (typeof state !== "undefined") state.cherryPickDropdownIndex = prevIdx;
      items.forEach((item, idx) => {
        item.classList.toggle("is-active", idx === prevIdx);
        if (idx === prevIdx && typeof item.scrollIntoView === "function") {
          item.scrollIntoView({ block: "nearest" });
        }
      });
    } else if (e.key === "Enter") {
      const cur = (typeof state !== "undefined" && typeof state.cherryPickDropdownIndex === "number") ? state.cherryPickDropdownIndex : -1;
      if (cur >= 0 && items[cur]) {
        e.preventDefault();
        const selectedBranch = items[cur].getAttribute("data-branch");
        if (selectedBranch) setPgCherryPickBranch(selectedBranch);
      }
    } else if (e.key === "Escape") {
      dropdownEl.style.display = "none";
    }
  }

  function openPgCherryPickDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.cherryPickDialogOpen = true;
    state.cherryPickDialogCl = num;
    state.cherryPickDropdownIndex = -1;

    const backdrop = document.getElementById("pgCherryPickBackdrop");
    const subEl = document.getElementById("pgCherryPickSubtitle");
    const branchInp = document.getElementById("pgCherryPickBranchInput");
    const baseInp = document.getElementById("pgCherryPickBaseInput");
    const topicInp = document.getElementById("pgCherryPickTopicInput");
    const msgInp = document.getElementById("pgCherryPickMessageInput");
    const srcTag = document.getElementById("pgCherryPickSourceTag");
    const conflictBanner = document.getElementById("pgCherryPickConflictBanner");
    const sameBranchBanner = document.getElementById("pgCherryPickSameBranchBanner");
    const allowConflictsCb = document.getElementById("pgCherryPickAllowConflicts");
    const keepReviewersCb = document.getElementById("pgCherryPickKeepReviewers");
    const dropdownEl = document.getElementById("pgCherryPickBranchDropdown");

    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""} (PS ${c.patch_set || 1})`;
    if (srcTag) {
      const proj = c.project || "platform/hardware/google/aemu";
      const br = c.branch || "main";
      srcTag.textContent = `Source Branch: ${br} · Project: ${proj}`;
    }

    if (branchInp) {
      branchInp.value = "";
    }
    if (baseInp) {
      baseInp.value = "";
    }
    if (topicInp) {
      topicInp.value = c.topic || "";
    }
    if (msgInp) {
      const origMsg = (c.commit_message || c.subject || "").trim();
      const origSha = c.current_revision || (c.revisions && Object.keys(c.revisions)[0]) || "";
      const cherryNote = origSha ? `\n\n(cherry picked from commit ${origSha.slice(0, 12)})` : "";
      msgInp.value = origMsg ? (origMsg.includes("(cherry picked from commit") ? origMsg : `${origMsg}${cherryNote}`) : "";
    }

    // Detect merge conflict
    const isConflict = (c.mergeable === false) || (String(c.status || "").toLowerCase() === "conflict");
    if (conflictBanner) {
      conflictBanner.style.display = isConflict ? "flex" : "none";
    }
    if (sameBranchBanner) {
      sameBranchBanner.style.display = "none";
    }
    if (allowConflictsCb) {
      allowConflictsCb.checked = isConflict;
    }
    if (keepReviewersCb) {
      keepReviewersCb.checked = true;
    }
    if (dropdownEl) {
      dropdownEl.style.display = "none";
    }

    renderPgCherryPickBranchSuggestions(num, "");
    renderPgCherryPickBaseSuggestions(num, "");

    const confirmBtn = document.getElementById("btnConfirmCherryPick");
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Cherry Pick";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (branchInp) branchInp.focus();
    }, 50);
  }

  function closePgCherryPickDialog() {
    state.cherryPickDialogOpen = false;
    state.cherryPickDialogCl = null;
    state.cherryPickDropdownIndex = -1;
    const backdrop = document.getElementById("pgCherryPickBackdrop");
    const dropdownEl = document.getElementById("pgCherryPickBranchDropdown");
    if (dropdownEl) {
      dropdownEl.style.display = "none";
    }
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgCherryPickBackdropClick(e) {
    if (e.target && e.target.id === "pgCherryPickBackdrop") {
      closePgCherryPickDialog();
    }
  }

  function setPgCherryPickBranch(branchName) {
    const branchInp = document.getElementById("pgCherryPickBranchInput");
    const dropdownEl = document.getElementById("pgCherryPickBranchDropdown");
    if (branchInp) {
      branchInp.value = branchName;
      branchInp.focus();
    }
    if (dropdownEl) {
      dropdownEl.style.display = "none";
    }
    const num = (typeof state !== "undefined" && (state.cherryPickDialogCl || state.selectedClNumber)) || 0;
    renderPgCherryPickBranchSuggestions(num, branchName);
    updatePgCherryPickSameBranchWarning(branchName);
  }

  async function executePgCherryPick() {
    const num = state.cherryPickDialogCl || state.selectedClNumber;
    if (!num) return;
    const branchInp = document.getElementById("pgCherryPickBranchInput");
    const baseInp = document.getElementById("pgCherryPickBaseInput");
    const topicInp = document.getElementById("pgCherryPickTopicInput");
    const msgInp = document.getElementById("pgCherryPickMessageInput");
    const allowConflictsCb = document.getElementById("pgCherryPickAllowConflicts");
    const keepReviewersCb = document.getElementById("pgCherryPickKeepReviewers");

    const dest = (branchInp && branchInp.value.trim()) || "";
    if (!dest) {
      showToast("Please enter a destination branch.");
      if (branchInp) branchInp.focus();
      return;
    }

    const baseVal = (baseInp && baseInp.value.trim()) || null;
    const topicVal = (topicInp && topicInp.value.trim()) || null;
    const msgVal = (msgInp && msgInp.value.trim()) || "";
    const allowConflicts = Boolean(allowConflictsCb && allowConflictsCb.checked);
    const keepReviewers = keepReviewersCb ? keepReviewersCb.checked : true;

    const confirmBtn = document.getElementById("btnConfirmCherryPick");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Cherry-picking…";
    }

    try {
      const payload = {
        cl: num,
        destination: dest,
        base: baseVal,
        message: msgVal,
        allow_conflicts: allowConflicts,
        keep_reviewers: keepReviewers,
        viewer: state.viewer,
      };
      if (topicVal) {
        payload.topic = topicVal;
      }
      const resp = await sidecarFetch("/api/gerrit/cherry_pick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      savePgRecentCherryPickBranch(dest);
      closePgCherryPickDialog();
      const newCl = data.new_cl || (data.response && (data.response._number || data.response.change_id));
      const newClStr = newCl ? ` New change: ag/${newCl}` : "";
      const conflictMsg = allowConflicts ? " (conflicts allowed)" : "";
      const topicMsg = topicVal ? ` (topic: ${topicVal})` : "";
      showToast(`✓ ag/${num} cherry-picked to ${dest}${topicMsg}${conflictMsg}!${newClStr}`);

      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
      if (typeof refreshQueueAsync === "function") {
        await refreshQueueAsync();
      }
    } catch (err) {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Cherry Pick";
      }
      showToast(`Cherry-pick failed: ${err.message || err}`);
    }
  }

  function openPgFollowUpDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.followUpDialogOpen = true;
    state.followUpDialogCl = num;

    const backdrop = document.getElementById("pgFollowUpBackdrop");
    const subEl = document.getElementById("pgFollowUpSubtitle");
    const branchInp = document.getElementById("pgFollowUpBranchInput");
    const baseInp = document.getElementById("pgFollowUpBaseInput");
    const topicInp = document.getElementById("pgFollowUpTopicInput");
    const msgInp = document.getElementById("pgFollowUpMessageInput");
    const wipCb = document.getElementById("pgFollowUpWipCheckbox");
    const privCb = document.getElementById("pgFollowUpPrivateCheckbox");

    if (subEl) subEl.textContent = `ag/${num}: ${c.subject || ""} (PS ${c.patch_set || 1})`;
    if (branchInp) {
      branchInp.value = c.branch || "goog/studio-main";
    }
    if (baseInp) {
      baseInp.value = `ag/${num}`;
    }
    if (topicInp) {
      topicInp.value = c.topic || "";
    }
    if (msgInp) {
      msgInp.value = `Follow-up: ${c.subject || ""}\n\n`;
    }
    if (wipCb) wipCb.checked = false;
    if (privCb) privCb.checked = false;

    const confirmBtn = document.getElementById("btnConfirmFollowUp");
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Create Change";
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => {
      if (msgInp) {
        msgInp.focus();
        msgInp.setSelectionRange(msgInp.value.length, msgInp.value.length);
      }
    }, 50);
  }

  function closePgFollowUpDialog() {
    state.followUpDialogOpen = false;
    state.followUpDialogCl = null;
    const backdrop = document.getElementById("pgFollowUpBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgFollowUpBackdropClick(e) {
    if (e.target && e.target.id === "pgFollowUpBackdrop") {
      closePgFollowUpDialog();
    }
  }

  function setPgFollowUpBranch(branchName) {
    const branchInp = document.getElementById("pgFollowUpBranchInput");
    if (branchInp) {
      branchInp.value = branchName;
      branchInp.focus();
    }
  }

  async function executePgFollowUp() {
    const num = state.followUpDialogCl || state.selectedClNumber;
    if (!num) return;
    const branchInp = document.getElementById("pgFollowUpBranchInput");
    const baseInp = document.getElementById("pgFollowUpBaseInput");
    const topicInp = document.getElementById("pgFollowUpTopicInput");
    const msgInp = document.getElementById("pgFollowUpMessageInput");
    const wipCb = document.getElementById("pgFollowUpWipCheckbox");
    const privCb = document.getElementById("pgFollowUpPrivateCheckbox");

    const branch = (branchInp && branchInp.value.trim()) || "";
    if (!branch) {
      showToast("Please enter a destination branch.");
      if (branchInp) branchInp.focus();
      return;
    }

    const subject = (msgInp && msgInp.value.trim()) || "";
    if (!subject) {
      showToast("Please enter a commit message / subject.");
      if (msgInp) msgInp.focus();
      return;
    }

    const baseVal = (baseInp && baseInp.value.trim()) || null;
    const topicVal = (topicInp && topicInp.value.trim()) || null;
    const isWip = Boolean(wipCb && wipCb.checked);
    const isPrivate = Boolean(privCb && privCb.checked);

    const confirmBtn = document.getElementById("btnConfirmFollowUp");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Creating change…";
    }

    try {
      const resp = await sidecarFetch("/api/gerrit/follow-up", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cl: num,
          branch: branch,
          subject: subject,
          base_change: baseVal,
          topic: topicVal,
          work_in_progress: isWip,
          is_private: isPrivate,
          viewer: state.viewer,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || (data && data.message) || `HTTP ${resp.status}`);
      }

      closePgFollowUpDialog();
      const newCl = data.new_cl_number || data.new_cl || (data.new_change && (data.new_change._number || data.new_change.change_id));
      const newClStr = newCl ? ` New change: ag/${newCl}` : "";
      showToast(`✓ Created follow-up change stacked on ag/${num}!${newClStr}`);

      if (typeof invalidateClientQueueCache === "function") {
        invalidateClientQueueCache();
      }
      if (typeof refreshQueueAsync === "function") {
        await refreshQueueAsync();
      }
    } catch (err) {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Create Change";
      }
      showToast(`Failed to create follow-up change: ${err.message || err}`);
    }
  }

  function openPgIncludedInDialog(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    state.includedInDialogOpen = true;
    state.includedInDialogCl = num;
    state.includedInFilterQuery = "";
    state.includedInData = null;

    const backdrop = document.getElementById("pgIncludedInBackdrop");
    const subEl = document.getElementById("pgIncludedInSubtitle");
    const filterInp = document.getElementById("pgIncludedInFilterInput");
    const clearBtn = document.getElementById("btnIncludedInClearFilter");
    const statusBadge = document.getElementById("pgIncludedInStatusBadge");

    if (subEl) {
      subEl.textContent = `ag/${num}: ${c.subject || ""} (PS ${c.patch_set || 1})`;
    }
    if (filterInp) {
      filterInp.value = "";
    }
    if (clearBtn) {
      clearBtn.style.display = "none";
    }
    if (statusBadge) {
      const st = String(c.status || "NEW").toUpperCase();
      if (st === "MERGED") {
        statusBadge.innerHTML = '<span class="pg-status-pill status-merged">✓ Merged</span>';
      } else if (st === "ABANDONED") {
        statusBadge.innerHTML = '<span class="pg-status-pill status-abandoned">✕ Abandoned</span>';
      } else {
        statusBadge.innerHTML = '<span class="pg-status-pill status-open">⏳ In Review (Open)</span>';
      }
    }

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }

    loadPgIncludedInData(num, false);

    setTimeout(() => {
      if (filterInp) {
        filterInp.focus();
      }
    }, 50);
  }

  function closePgIncludedInDialog() {
    state.includedInDialogOpen = false;
    state.includedInDialogCl = null;
    state.includedInData = null;
    state.includedInFilterQuery = "";
    const backdrop = document.getElementById("pgIncludedInBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgIncludedInBackdropClick(e) {
    if (e.target && e.target.id === "pgIncludedInBackdrop") {
      closePgIncludedInDialog();
    }
  }

  async function loadPgIncludedInData(clNum, forceRefresh) {
    const reqSeq = (state.includedInReqSeq = (state.includedInReqSeq || 0) + 1);
    const isStale = () =>
      !state.includedInDialogOpen ||
      Number(state.includedInDialogCl) !== Number(clNum) ||
      state.includedInReqSeq !== reqSeq;

    const loadingEl = document.getElementById("pgIncludedInLoading");
    const errorEl = document.getElementById("pgIncludedInError");
    const contentEl = document.getElementById("pgIncludedInContent");

    if (loadingEl) loadingEl.style.display = "flex";
    if (errorEl) errorEl.style.display = "none";
    if (contentEl) contentEl.style.display = "none";

    try {
      const refreshParam = forceRefresh ? "&refresh=1" : "";
      const resp = await sidecarFetch(`/api/gerrit/included-in?cl=${encodeURIComponent(clNum)}${refreshParam}`);
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (!data || data.status === "error" || data.error) {
        throw new Error((data && data.error) || `Included-in lookup returned an error for ag/${clNum}`);
      }
      if (isStale()) return;
      state.includedInData = data;
      if (loadingEl) loadingEl.style.display = "none";
      if (contentEl) contentEl.style.display = "flex";
      renderPgIncludedInDom();
    } catch (err) {
      if (isStale()) return;
      if (loadingEl) loadingEl.style.display = "none";
      if (errorEl) {
        errorEl.style.display = "flex";
        const msgEl = document.getElementById("pgIncludedInErrorMsg");
        if (msgEl) {
          msgEl.textContent = `Failed to fetch included branches and tags for ag/${clNum}: ${err.message || err}. Run 'gcert' and click Refresh to retry.`;
        }
      }
    }
  }

  function refreshPgIncludedIn() {
    if (state.includedInDialogCl) {
      loadPgIncludedInData(state.includedInDialogCl, true);
    }
  }

  function onPgIncludedInFilterInput(val) {
    state.includedInFilterQuery = String(val || "").trim().toLowerCase();
    const clearBtn = document.getElementById("btnIncludedInClearFilter");
    if (clearBtn) {
      clearBtn.style.display = state.includedInFilterQuery ? "inline-flex" : "none";
    }
    renderPgIncludedInDom();
  }

  function clearPgIncludedInFilter() {
    state.includedInFilterQuery = "";
    const inp = document.getElementById("pgIncludedInFilterInput");
    if (inp) {
      inp.value = "";
      inp.focus();
    }
    const clearBtn = document.getElementById("btnIncludedInClearFilter");
    if (clearBtn) {
      clearBtn.style.display = "none";
    }
    renderPgIncludedInDom();
  }

  function copyPgIncludedItem(name, kind) {
    const itemKind = kind || "item";
    if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(name);
    }
    showToast(`Copied ${itemKind} '${name}' to clipboard`);
  }

  function renderPgIncludedInDom() {
    const data = state.includedInData || { branches: [], tags: [], external: {} };
    const query = state.includedInFilterQuery || "";

    const rawBranches = Array.isArray(data.branches) ? data.branches : [];
    const rawTags = Array.isArray(data.tags) ? data.tags : [];
    const rawExternal = (data.external && typeof data.external === "object") ? data.external : {};

    const filteredBranches = query
      ? rawBranches.filter(b => String(b).toLowerCase().includes(query))
      : rawBranches;
    const filteredTags = query
      ? rawTags.filter(t => String(t).toLowerCase().includes(query))
      : rawTags;

    const branchesSec = document.getElementById("pgIncludedInBranchesSection");
    const branchesCount = document.getElementById("pgIncludedInBranchesCount");
    const branchesList = document.getElementById("pgIncludedInBranchesList");

    const tagsSec = document.getElementById("pgIncludedInTagsSection");
    const tagsCount = document.getElementById("pgIncludedInTagsCount");
    const tagsList = document.getElementById("pgIncludedInTagsList");

    const emptyEl = document.getElementById("pgIncludedInEmptyState");

    if (branchesCount) branchesCount.textContent = String(filteredBranches.length);
    if (branchesList) {
      if (filteredBranches.length === 0) {
        branchesList.innerHTML = `<span style="font-size:12px; color:var(--g-muted); font-style:italic;">${rawBranches.length === 0 ? "No branches" : "No matching branches"}</span>`;
      } else {
        branchesList.innerHTML = filteredBranches.map(b => {
          const safeB = escapeHtml(String(b));
          return `<div class="pg-included-item" title="Branch: ${safeB}">
            <span class="pg-included-item-icon" aria-hidden="true">🌿</span>
            <span class="pg-included-item-name">${safeB}</span>
            <button type="button" class="pg-included-item-copy" onclick="copyPgIncludedItem('${safeB}', 'branch')" title="Copy branch name">📋</button>
          </div>`;
        }).join("");
      }
    }

    if (tagsCount) tagsCount.textContent = String(filteredTags.length);
    if (tagsList) {
      if (filteredTags.length === 0) {
        tagsList.innerHTML = `<span style="font-size:12px; color:var(--g-muted); font-style:italic;">${rawTags.length === 0 ? "No tags" : "No matching tags"}</span>`;
      } else {
        tagsList.innerHTML = filteredTags.map(t => {
          const safeT = escapeHtml(String(t));
          return `<div class="pg-included-item" title="Tag: ${safeT}">
            <span class="pg-included-item-icon" aria-hidden="true">🏷️</span>
            <span class="pg-included-item-name">${safeT}</span>
            <button type="button" class="pg-included-item-copy" onclick="copyPgIncludedItem('${safeT}', 'tag')" title="Copy tag name">📋</button>
          </div>`;
        }).join("");
      }
    }

    const hasAnyResults = filteredBranches.length > 0 || filteredTags.length > 0;
    const hasAnyRaw = rawBranches.length > 0 || rawTags.length > 0;

    if (!emptyEl) return;
    if (hasAnyResults) {
      emptyEl.style.display = "none";
      return;
    }
    emptyEl.style.display = "block";
    if (query && hasAnyRaw) {
      emptyEl.innerHTML = `No branches or tags match "<strong>${escapeHtml(query)}</strong>". <button type="button" class="btn pg-btn-flat" onclick="clearPgIncludedInFilter()" style="margin-left:6px;">Clear filter</button>`;
      return;
    }
    const st = String((data && data.change_status) || "").toUpperCase();
    if (st === "MERGED") {
      emptyEl.textContent = "This commit has been merged, but is not yet included in any tagged release branches.";
    } else if (st === "ABANDONED") {
      emptyEl.textContent = "This change is abandoned.";
    } else {
      emptyEl.textContent = "This change is currently open in review and has not yet been merged into release branches or tags.";
    }
  }

  function getActivePgFileDiffBlock() {
    const changeView = document.querySelector(".pg-change-view");
    if (!changeView) return null;
    const num = Number(state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const container = document.getElementById("diffPane") || document.getElementById("diffScroll") || window;
    const viewTop = container.scrollTop !== undefined ? container.scrollTop : 0;

    const commitMsgPanel = (num && document.getElementById(`commitMsgPanel-${num}`)) || changeView.querySelector(".commit-msg-panel:not(.merge-list-panel)");
    if (commitMsgPanel && commitMsgPanel.style.display !== "none") {
      const top = commitMsgPanel.offsetTop || 0;
      const height = commitMsgPanel.offsetHeight || 0;
      if (top + height >= viewTop + 40 && viewTop <= top + height) {
        return commitMsgPanel;
      }
    }
    const mergeListPanel = (num && document.getElementById(`mergeListPanel-${num}`)) || changeView.querySelector(".merge-list-panel");
    if (mergeListPanel && mergeListPanel.style.display !== "none") {
      const top = mergeListPanel.offsetTop || 0;
      const height = mergeListPanel.offsetHeight || 0;
      if (top + height >= viewTop + 40 && viewTop <= top + height) {
        return mergeListPanel;
      }
    }

    const blocks = Array.from(changeView.querySelectorAll(".pg-file-diff-block"));
    if (!blocks.length) {
      if (commitMsgPanel && commitMsgPanel.style.display !== "none") return commitMsgPanel;
      return null;
    }
    for (let i = 0; i < blocks.length; i++) {
      const top = blocks[i].offsetTop || 0;
      const height = blocks[i].offsetHeight || 0;
      if (top + height >= viewTop + 40) {
        return blocks[i];
      }
    }
    return blocks[0];
  }

  function togglePgActiveFileReviewed(opts = {}) {
    const num = Number(state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const block = getActivePgFileDiffBlock();
    let clNum = block ? Number(block.getAttribute("data-cl")) : num;
    let ps = block ? Number(block.getAttribute("data-ps") || 1) : 1;
    let filePath = block ? (block.getAttribute("data-file") || "") : "";

    if (!filePath && num) {
      filePath = (state.focusedFileByCl && state.focusedFileByCl[num]) || (state.activeFileByCl && state.activeFileByCl[num]) || "";
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
      const c = (state.changes || []).find(ch => ch.number === num);
      ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);
      clNum = num;
    }
    if (!filePath || !clNum) return;

    const isRev = isPgFileReviewed(clNum, ps, filePath);
    const nextRev = !isRev;
    setPgFileReviewed(clNum, ps, filePath, nextRev);
    state.lastReviewedCheckbox = { cl: clNum, ps, file: filePath, checked: nextRev };
    focusPgFileRow(clNum, filePath, { scrollIntoView: true, focusElement: true });

    if (typeof showToast === "function") {
      const shortName = filePath.split("/").pop() || filePath;
      if (nextRev) {
        const unrev = (typeof getPgUnreviewedFiles === "function") ? getPgUnreviewedFiles(clNum, ps) : [];
        if (unrev.length === 0) {
          showToast(`Marked ${shortName} as reviewed ✓ (All files reviewed! 🎉)`);
        } else {
          showToast(`Marked ${shortName} as reviewed ✓ (${unrev.length} unreviewed left · Alt+] for next)`);
        }
      } else {
        showToast(`Marked ${shortName} as unreviewed (f)`);
      }
    }

    if (nextRev && opts.advance !== false) {
      navigatePgFileDiff("next", filePath, clNum);
    }
  }

  function markActiveFileReviewedAndGoToNextUnreviewed(clNum = null, filePath = null, patchSet = null) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    let targetFile = filePath;
    let ps = Number(patchSet || 1);
    if (!targetFile) {
      const block = (typeof getActivePgFileDiffBlock === "function") ? getActivePgFileDiffBlock() : null;
      if (block) {
        targetFile = block.getAttribute("data-file") || "";
        ps = Number(block.getAttribute("data-ps") || 1);
      }
    }
    if (!targetFile && num) {
      targetFile = (state.focusedFileByCl && state.focusedFileByCl[num]) || (state.activeFileByCl && state.activeFileByCl[num]) || "";
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
      const c = (state.changes || []).find(ch => ch.number === num);
      ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);
    }
    if (!targetFile || !num) {
      if (typeof navigatePgUnreviewedFileDiff === "function") {
        return navigatePgUnreviewedFileDiff("next", null, num);
      }
      return null;
    }

    setPgFileReviewed(num, ps, targetFile, true);
    state.lastReviewedCheckbox = { cl: num, ps, file: targetFile, checked: true };
    const shortName = targetFile.split("/").pop() || targetFile;

    const unrev = (typeof getPgUnreviewedFiles === "function") ? getPgUnreviewedFiles(num, ps) : [];
    if (unrev.length === 0) {
      if (typeof showToast === "function") {
        showToast(`Marked ${shortName} as reviewed ✓ — All files in this change are now reviewed! 🎉`);
      }
      return null;
    }

    const nextTargetFile = unrev[0];
    if (typeof navigatePgUnreviewedFileDiff === "function") {
      navigatePgUnreviewedFileDiff("next", targetFile, num);
    }

    if (typeof showToast === "function") {
      const nextShort = nextTargetFile ? (nextTargetFile.split("/").pop() || nextTargetFile) : "";
      if (nextShort) {
        showToast(`Marked ${shortName} as reviewed ✓ → Jumped to next unreviewed: ${nextShort} (${unrev.length} left · M)`);
      } else {
        showToast(`Marked ${shortName} as reviewed ✓ (${unrev.length} unreviewed left · M)`);
      }
    }
    return nextTargetFile;
  }

  function togglePgActiveFileDiffCollapse() {
    const num = Number(state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    const block = getActivePgFileDiffBlock();
    let clNum = block ? Number(block.getAttribute("data-cl")) : num;
    let ps = block ? Number(block.getAttribute("data-ps") || 1) : 1;
    let filePath = block ? (block.getAttribute("data-file") || "") : "";

    if (!filePath && num) {
      filePath = (state.focusedFileByCl && state.focusedFileByCl[num]) || (state.activeFileByCl && state.activeFileByCl[num]) || "";
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
      const c = (state.changes || []).find(ch => ch.number === num);
      ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);
      clNum = num;
    }
    if (!filePath || !clNum) return;
    togglePgFileDiffBlock(null, clNum, ps, filePath);
  }

  function renderPgHeaderActionButtonsHtml(c) {
    if (!c) return "";
    const num = Number(c.number);
    const hasActiveEdit = Boolean(state.activeEditsByCl && state.activeEditsByCl[num]);
    return AemuGerritReact.renderPgHeaderActionButtonsHtml(c, hasActiveEdit);
  }

  function openPgFileEditor(clNum, filePath, psNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number));
    const c = resolvePgChangeForAction(num);
    if (!c) return;

    let targetFile = filePath;
    let targetPs = psNum;

    if (!targetFile) {
      const activeBlock = getActivePgFileDiffBlock();
      if (activeBlock) {
        targetFile = activeBlock.getAttribute("data-file");
        if (!targetPs) {
          targetPs = Number(activeBlock.getAttribute("data-ps") || 1);
        }
      }
    }

    if (!targetFile) {
      const bundle = state.diffBundleByCl && state.diffBundleByCl[num];
      const files = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
      if (files.length > 0) {
        targetFile = files[0].file;
      }
    }

    if (targetFile === "/COMMIT_MSG") {
      openPgCommitMsgEditor(num);
      return;
    }

    if (!targetFile) {
      showToast("No editable file found for this change.");
      return;
    }

    if (!targetPs) {
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
      targetPs = Number(selPsMeta.ps || c.patch_set || 1);
    }

    state.fileEditorOpen = true;
    state.fileEditorState = {
      cl: num,
      file: targetFile,
      ps: targetPs,
      initialContent: "",
      isDirty: false,
      wrap: false,
      tabSize: (state.diffPreferences && state.diffPreferences.tabWidth) || 4,
    };

    const backdrop = document.getElementById("pgFileEditorBackdrop");
    const titleEl = document.getElementById("pgEditorTitle");
    const subEl = document.getElementById("pgEditorSubtitle");
    const statusBadge = document.getElementById("pgEditorStatusBadge");
    const dirtyBadge = document.getElementById("pgEditorDirtyBadge");
    const savedBadge = document.getElementById("pgEditorSavedBadge");
    const loadingEl = document.getElementById("pgEditorLoading");
    const errEl = document.getElementById("pgEditorError");
    const textarea = document.getElementById("pgEditorTextarea");
    const gutter = document.getElementById("pgEditorGutter");

    if (titleEl) {
      const baseName = targetFile.split("/").pop() || targetFile;
      titleEl.textContent = `Edit: ${baseName}`;
    }
    if (subEl) {
      subEl.textContent = `Patchset ${targetPs} · ${targetFile} · ag/${num}`;
    }
    if (statusBadge) {
      statusBadge.textContent = "M";
    }
    if (dirtyBadge) dirtyBadge.style.display = "none";
    if (savedBadge) savedBadge.style.display = "none";
    if (errEl) errEl.style.display = "none";
    if (textarea) {
      textarea.value = "";
      textarea.classList.remove("is-wrapped");
    }
    if (gutter) gutter.textContent = "1";
    updatePgEditorMetrics(textarea);

    if (backdrop) {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
    }

    if (loadingEl) loadingEl.style.display = "flex";
    sidecarFetch(`/api/gerrit/file-content?cl=${num}&file=${encodeURIComponent(targetFile)}&ps=${targetPs}`)
      .then(res => res.json())
      .then(data => {
        if (loadingEl) loadingEl.style.display = "none";
        const content = (data && typeof data.content === "string") ? data.content : "";
        if (state.fileEditorState && state.fileEditorState.cl === num && state.fileEditorState.file === targetFile) {
          state.fileEditorState.initialContent = content;
          state.fileEditorState.isDirty = false;
          if (textarea) {
            textarea.value = content;
            updatePgEditorGutter(content.split("\n").length);
            updatePgEditorMetrics(textarea);
            textarea.focus();
            if (textarea.setSelectionRange) {
              textarea.setSelectionRange(0, 0);
            }
          }
        }
      })
      .catch(err => {
        if (loadingEl) loadingEl.style.display = "none";
        if (errEl) {
          errEl.style.display = "flex";
          const msgEl = document.getElementById("pgEditorErrorMsg");
          if (msgEl) msgEl.textContent = `Failed to load ${targetFile}: ${err.message || err}`;
        }
      });
  }

  function closePgFileEditorDialog() {
    if (state.fileEditorState && state.fileEditorState.isDirty) {
      if (typeof confirm === "function" && !confirm("You have unsaved changes in this file. Discard them and close?")) {
        return;
      }
    }
    state.fileEditorOpen = false;
    state.fileEditorState = null;
    const backdrop = document.getElementById("pgFileEditorBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgFileEditorBackdropClick(e) {
    if (e.target && e.target.id === "pgFileEditorBackdrop") {
      closePgFileEditorDialog();
    }
  }

  function onPgFileEditClick(event, clNum, filePath, psNum) {
    if (event) event.stopPropagation();
    openPgFileEditor(clNum, filePath, psNum);
  }

  function onPgEditorInput(textarea) {
    if (!textarea || !state.fileEditorState) return;
    const isDirty = textarea.value !== state.fileEditorState.initialContent;
    state.fileEditorState.isDirty = isDirty;
    const dirtyBadge = document.getElementById("pgEditorDirtyBadge");
    const savedBadge = document.getElementById("pgEditorSavedBadge");
    if (dirtyBadge) dirtyBadge.style.display = isDirty ? "inline-flex" : "none";
    if (savedBadge && isDirty) savedBadge.style.display = "none";

    const lines = textarea.value.split("\n");
    updatePgEditorGutter(lines.length);
    updatePgEditorMetrics(textarea);
  }

  function onPgEditorScroll(textarea) {
    const gutter = document.getElementById("pgEditorGutter");
    if (gutter && textarea) {
      gutter.scrollTop = textarea.scrollTop;
    }
  }

  function onPgEditorCursorMove(textarea) {
    updatePgEditorMetrics(textarea);
  }

  function updatePgEditorGutter(lineCount) {
    const gutter = document.getElementById("pgEditorGutter");
    if (!gutter) return;
    const count = Math.max(1, lineCount || 1);
    let s = "";
    for (let i = 1; i <= count; i++) {
      s += i + "\n";
    }
    gutter.textContent = s;
  }

  function updatePgEditorMetrics(textarea) {
    const infoEl = document.getElementById("pgEditorCursorInfo");
    if (!infoEl || !textarea) return;
    const text = textarea.value || "";
    const lines = text.split("\n");
    const totalLines = lines.length;
    const selStart = textarea.selectionStart || 0;
    const beforeText = text.slice(0, selStart);
    const beforeLines = beforeText.split("\n");
    const curLine = beforeLines.length;
    const curCol = (beforeLines[beforeLines.length - 1] || "").length + 1;
    const byteCount = typeof Blob !== "undefined" ? new Blob([text]).size : (typeof Buffer !== "undefined" ? Buffer.byteLength(text, "utf8") : text.length);
    const sizeDisplay = byteCount >= 1024 ? `${(byteCount / 1024).toFixed(1)} KB` : `${byteCount} B`;
    infoEl.textContent = `Ln ${curLine}, Col ${curCol} · ${totalLines} line${totalLines === 1 ? "" : "s"} · ${sizeDisplay}`;
  }

  function togglePgEditorWrap() {
    if (!state.fileEditorState) return;
    const nextWrap = !state.fileEditorState.wrap;
    state.fileEditorState.wrap = nextWrap;
    const textarea = document.getElementById("pgEditorTextarea");
    const btn = document.getElementById("btnEditorToggleWrap");
    if (textarea) {
      if (nextWrap) {
        textarea.classList.add("is-wrapped");
      } else {
        textarea.classList.remove("is-wrapped");
      }
    }
    if (btn) {
      btn.textContent = nextWrap ? "Wrap: On" : "Wrap: Off";
    }
  }

  function cyclePgEditorTabSize() {
    if (!state.fileEditorState) return;
    const sizes = [2, 4, 8];
    const cur = state.fileEditorState.tabSize || 4;
    const next = sizes[(sizes.indexOf(cur) + 1) % sizes.length];
    state.fileEditorState.tabSize = next;
    const textarea = document.getElementById("pgEditorTextarea");
    const btn = document.getElementById("btnEditorToggleTabSize");
    if (textarea) {
      textarea.style.tabSize = String(next);
      textarea.style.MozTabSize = String(next);
    }
    if (btn) {
      btn.textContent = `Tab: ${next}`;
    }
  }

  function onPgEditorKeyDown(event, textarea) {
    if (!textarea || !state.fileEditorState) return;

    if ((event.ctrlKey || event.metaKey) && event.key === "s") {
      event.preventDefault();
      saveActiveFileEdit();
      return;
    }

    const tabSize = state.fileEditorState.tabSize || 4;
    const tabSpaces = " ".repeat(tabSize);

    if (event.key === "Tab") {
      event.preventDefault();
      const start = textarea.selectionStart || 0;
      const end = textarea.selectionEnd || 0;
      if (start === end) {
        if (typeof document !== "undefined" && document.execCommand) {
          document.execCommand("insertText", false, tabSpaces);
        } else {
          const text = textarea.value;
          textarea.value = text.substring(0, start) + tabSpaces + text.substring(start);
          if (textarea.setSelectionRange) textarea.setSelectionRange(start + tabSpaces.length, start + tabSpaces.length);
        }
        onPgEditorInput(textarea);
      } else {
        const text = textarea.value;
        const before = text.substring(0, start);
        const selected = text.substring(start, end);
        const after = text.substring(end);
        if (event.shiftKey) {
          const unindented = selected.replace(new RegExp(`^ {1,${tabSize}}`, "gm"), "");
          textarea.value = before + unindented + after;
          if (textarea.setSelectionRange) textarea.setSelectionRange(start, start + unindented.length);
        } else {
          const indented = selected.replace(/^/gm, tabSpaces);
          textarea.value = before + indented + after;
          if (textarea.setSelectionRange) textarea.setSelectionRange(start, start + indented.length);
        }
        onPgEditorInput(textarea);
      }
      return;
    }

    if (event.key === "Enter" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) {
      const start = textarea.selectionStart || 0;
      const text = textarea.value;
      const beforeCursor = text.substring(0, start);
      const lineStart = beforeCursor.lastIndexOf("\n") + 1;
      const currentLine = beforeCursor.substring(lineStart);
      const match = currentLine.match(/^[ \t]+/);
      if (match) {
        event.preventDefault();
        const indent = match[0];
        if (typeof document !== "undefined" && document.execCommand) {
          document.execCommand("insertText", false, "\n" + indent);
        } else {
          textarea.value = beforeCursor + "\n" + indent + text.substring(start);
          if (textarea.setSelectionRange) textarea.setSelectionRange(start + 1 + indent.length, start + 1 + indent.length);
        }
        onPgEditorInput(textarea);
        return;
      }
    }
  }

  function saveActiveFileEdit() {
    if (!state.fileEditorState) return Promise.resolve(null);
    const { cl, file } = state.fileEditorState;
    const textarea = document.getElementById("pgEditorTextarea");
    const content = textarea ? textarea.value : "";
    const saveBtn = document.getElementById("btnSaveFileEdit");
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
    }

    return sidecarFetch("/api/gerrit/edit-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cl, file, content }),
    })
      .then(res => res.json())
      .then(data => {
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.textContent = "💾 Save";
        }
        if (data && data.status === "error") {
          showToast(`Save failed: ${data.error || "Unknown error"}`);
          return data;
        }
        if (state.fileEditorState) {
          state.fileEditorState.initialContent = content;
          state.fileEditorState.isDirty = false;
        }
        state.activeEditsByCl = state.activeEditsByCl || {};
        state.activeEditsByCl[cl] = true;

        const dirtyBadge = document.getElementById("pgEditorDirtyBadge");
        const savedBadge = document.getElementById("pgEditorSavedBadge");
        if (dirtyBadge) dirtyBadge.style.display = "none";
        if (savedBadge) {
          savedBadge.style.display = "inline-flex";
          setTimeout(() => {
            if (savedBadge && !state.fileEditorState?.isDirty) savedBadge.style.display = "none";
          }, 3000);
        }

        showToast(`✓ Saved ${file} to change edit`);
        updatePgHeaderActionsDom(cl);
        return data;
      })
      .catch(err => {
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.textContent = "💾 Save";
        }
        showToast(`Save failed: ${err.message || err}`);
        throw err;
      });
  }

  function saveAndPublishActiveFileEdit() {
    if (!state.fileEditorState) return;
    const cl = state.fileEditorState.cl;
    saveActiveFileEdit()
      .then(() => publishActiveChangeEdit(cl))
      .catch(() => {});
  }

  function publishActiveChangeEdit(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return Promise.resolve();
    showToast(`Publishing change edit for ag/${num}…`);
    return sidecarFetch("/api/gerrit/publish-edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cl: num, notify: "OWNER" }),
    })
      .then(res => res.json())
      .then(data => {
        if (data && data.status === "error") {
          showToast(`Publish edit failed: ${data.error || "Unknown error"}`);
          return;
        }
        state.activeEditsByCl = state.activeEditsByCl || {};
        state.activeEditsByCl[num] = false;
        closePgFileEditorDialog();
        showToast(`✓ Published change edit for ag/${num} as new patchset`);
        updatePgHeaderActionsDom(num);
        if (typeof refreshQueue === "function") refreshQueue();
        if (typeof fetchDiffsForNumbers === "function") fetchDiffsForNumbers([num], true);
      })
      .catch(err => {
        showToast(`Publish edit failed: ${err.message || err}`);
      });
  }

  function discardActiveChangeEdit(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return Promise.resolve();
    if (typeof confirm === "function" && !confirm(`Discard all changes in change edit for ag/${num}? This cannot be undone.`)) {
      return Promise.resolve();
    }
    showToast(`Discarding change edit for ag/${num}…`);
    return sidecarFetch("/api/gerrit/discard-edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cl: num }),
    })
      .then(res => res.json())
      .then(data => {
        if (data && data.status === "error") {
          showToast(`Discard edit failed: ${data.error || "Unknown error"}`);
          return;
        }
        state.activeEditsByCl = state.activeEditsByCl || {};
        state.activeEditsByCl[num] = false;
        closePgFileEditorDialog();
        showToast(`✓ Discarded change edit for ag/${num}`);
        updatePgHeaderActionsDom(num);
        if (typeof refreshQueue === "function") refreshQueue();
        if (typeof fetchDiffsForNumbers === "function") fetchDiffsForNumbers([num], true);
      })
      .catch(err => {
        showToast(`Discard edit failed: ${err.message || err}`);
      });
  }

  function updatePgHeaderActionsDom(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    if (!num) return;
    const c = resolvePgChangeForAction(num);
    if (!c) return;
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`);
    if (changeView) {
      const groupEl = changeView.querySelector(".pg-header-action-group");
      if (groupEl) {
        groupEl.outerHTML = renderPgHeaderActionButtonsHtml(c);
      }
    }
  }

  const PG_VALID_COMMIT_TYPES = new Set([
    "feat", "fix", "build", "docs", "chore", "test", "refactor", "perf", "style"
  ]);

  function validatePgCommitMessage(text) {
    const raw = String(text || "");
    const lines = raw.split(/\r?\n/);
    const nonBlank = lines.map((l, idx) => ({ text: l, idx })).filter(x => x.text.trim().length > 0);
    const subject = nonBlank.length > 0 ? nonBlank[0].text.trim() : "";
    const subjectLen = subject.length;

    const subjectValid = subjectLen > 0 && subjectLen <= 50;
    const subjectWarning = subjectLen > 50 && subjectLen <= 72;
    const subjectError = subjectLen > 72 || subjectLen === 0;

    let hasTypePrefix = false;
    let typePrefix = "";
    let typeScope = "";
    const typeMatch = subject.match(/^([a-z]+)(?:\(([^)]+)\))?!?:\s*(.*)$/i);
    if (typeMatch) {
      const candType = typeMatch[1].toLowerCase();
      if (PG_VALID_COMMIT_TYPES.has(candType)) {
        hasTypePrefix = true;
        typePrefix = candType;
        typeScope = typeMatch[2] || "";
      }
    }

    const hasTrailingPeriod = subject.endsWith(".");

    const subjectLineIdx = nonBlank.length > 0 ? nonBlank[0].idx : 0;
    const bodyLines = lines.slice(subjectLineIdx + 1);
    let maxLineLen = 0;
    const longLines = [];

    for (let i = 0; i < bodyLines.length; i++) {
      const bLine = bodyLines[i];
      const trimmed = bLine.trim();
      const isUrl = /^https?:\/\//i.test(trimmed);
      const lineLen = bLine.length;
      if (!isUrl && lineLen > maxLineLen) {
        maxLineLen = lineLen;
      }
      if (!isUrl && lineLen > 72) {
        longLines.push({ lineNum: subjectLineIdx + 1 + i + 1, len: lineLen });
      }
    }

    const hasBugFooter = /^(Bug|Fixes|Close[sd]?):\s*(\S+)/mi.test(raw);
    const bugMatch = raw.match(/^(?:Bug|Fixes|Close[sd]?):\s*(\S+)/mi);
    const bugId = bugMatch ? bugMatch[1] : "";

    const hasTestFooter = /^Test:\s*(\S.*)$/mi.test(raw);
    const testMatch = raw.match(/^Test:\s*(\S.*)$/mi);
    const testCmd = testMatch ? testMatch[1].trim() : "";

    const hasChangeId = /^Change-Id:\s*(I[0-9a-fA-F]{40})/mi.test(raw);
    const changeIdMatch = raw.match(/^Change-Id:\s*(I[0-9a-fA-F]{40})/mi);
    const changeId = changeIdMatch ? changeIdMatch[1] : "";

    const isValid = subjectValid && hasTypePrefix && !hasTrailingPeriod && longLines.length === 0;

    return {
      raw,
      subject,
      subjectLen,
      subjectValid,
      subjectWarning,
      subjectError,
      hasTypePrefix,
      typePrefix,
      typeScope,
      hasTrailingPeriod,
      maxLineLen,
      longLines,
      hasBugFooter,
      bugId,
      hasTestFooter,
      testCmd,
      hasChangeId,
      changeId,
      isValid,
    };
  }

  function renderPgCommitMsgMetricsHtml(metrics) {
    if (!metrics) return "";
    const chips = [];

    // 1. Subject chip
    if (metrics.subjectError) {
      chips.push(`<span class="pg-commit-metric-chip chip-subject is-error" title="Subject is ${metrics.subjectLen} characters (limit 50, max 72)">✕ Subject: ${metrics.subjectLen}/50</span>`);
    } else if (metrics.subjectWarning) {
      chips.push(`<span class="pg-commit-metric-chip chip-subject is-warning" title="Subject is ${metrics.subjectLen} characters (>50 recommends <=50)">▲ Subject: ${metrics.subjectLen}/50</span>`);
    } else {
      chips.push(`<span class="pg-commit-metric-chip chip-subject is-valid" title="Subject is ${metrics.subjectLen} characters (<=50)">✓ Subject: ${metrics.subjectLen}/50</span>`);
    }

    // 2. Max body line chip
    if (metrics.longLines && metrics.longLines.length > 0) {
      const firstLong = metrics.longLines[0];
      const count = metrics.longLines.length;
      const countLabel = count === 1 ? `Line ${firstLong.lineNum} (${firstLong.len}c > 72)` : `${count} lines > 72c`;
      chips.push(`<span class="pg-commit-metric-chip chip-body is-error" title="${count} line(s) exceed 72 characters. Click 'Wrap at 72' to fix.">✕ ${countLabel}</span>`);
    } else if (metrics.maxLineLen > 0) {
      chips.push(`<span class="pg-commit-metric-chip chip-body is-valid" title="Maximum body line length is ${metrics.maxLineLen} characters (<= 72)">✓ Max line: ${metrics.maxLineLen}/72</span>`);
    }

    // 3. Type prefix chip
    if (metrics.hasTypePrefix) {
      const scopeLabel = metrics.typeScope ? `(${metrics.typeScope})` : "";
      chips.push(`<span class="pg-commit-metric-chip chip-type is-valid" title="Valid semantic type '${metrics.typePrefix}'">✓ ${metrics.typePrefix}${scopeLabel}</span>`);
    } else {
      chips.push(`<span class="pg-commit-metric-chip chip-type is-warning" title="Missing semantic type prefix (e.g. feat:, fix:, docs:, refactor:, test:)">▲ Type prefix</span>`);
    }

    // 4. Trailing period check
    if (metrics.hasTrailingPeriod) {
      chips.push(`<span class="pg-commit-metric-chip chip-period is-warning" title="Subject line has trailing period (imperative mood recommends no period)">▲ Trailing period</span>`);
    }

    // 5. Footers checklist
    if (metrics.hasBugFooter) {
      const mBugNum = String(metrics.bugId || "").match(/(\d{6,12})/);
      const bugHoverAttrs = mBugNum
        ? ` data-bug-id="${escapeHtml(mBugNum[1])}" onmouseenter="showPgBugHovercard(event, '${escapeHtml(mBugNum[1])}')" onmouseleave="scheduleHidePgBugHovercard()"`
        : "";
      chips.push(`<span class="pg-commit-metric-chip chip-footer-bug is-valid"${bugHoverAttrs} title="Bug footer present: ${escapeHtml(metrics.bugId)}">✓ Bug</span>`);
    } else {
      chips.push(`<span class="pg-commit-metric-chip chip-footer-bug is-warning" title="Missing Bug: footer (use Bug: N/A if none)">▲ Bug</span>`);
    }

    if (metrics.hasTestFooter) {
      chips.push(`<span class="pg-commit-metric-chip chip-footer-test is-valid" title="Test footer present">✓ Test</span>`);
    } else {
      chips.push(`<span class="pg-commit-metric-chip chip-footer-test is-warning" title="Missing Test: footer">▲ Test</span>`);
    }

    if (metrics.hasChangeId) {
      chips.push(`<span class="pg-commit-metric-chip chip-footer-changeid is-valid" title="Change-Id footer present">✓ Change-Id</span>`);
    } else {
      chips.push(`<span class="pg-commit-metric-chip chip-footer-changeid is-error" title="Missing Change-Id: footer">✕ Change-Id</span>`);
    }

    return chips.join("");
  }

  function formatWrappedCommitMessage(text, maxCols = 72) {
    if (!text || typeof text !== "string") return "";
    const lines = text.split(/\r?\n/);
    if (lines.length <= 1) return text;

    const result = [];
    result.push(lines[0]);

    const isFooter = line => /^(Bug|Test|Tested-by|Change-Id|Original-Change-Id|Signed-off-by|Reviewed-on|Reviewed-by|Co-authored-by|Acked-by|Cc|Reported-by|Suggested-by|Commit-Ready|Part-of|Release-Notes|Fixes|Issue|Close[sd]?|BREAKING CHANGE|Tag=|CONV=):/i.test(line.trim());
    const isSpecial = line => {
      const trimmed = line.trim();
      if (!trimmed) return true;
      if (isFooter(line)) return true;
      if (/^https?:\/\//i.test(trimmed)) return true;
      if (/^[#*>-]/.test(trimmed)) return true;
      if (/^\d+\.\s/.test(trimmed)) return true;
      if (/^\s{2,}|\t/.test(line)) return true;
      return false;
    };

    let pBuffer = [];

    const flushParagraph = () => {
      if (pBuffer.length === 0) return;
      const fullPara = pBuffer.join(" ");
      pBuffer = [];
      const words = fullPara.split(/\s+/).filter(Boolean);
      let curLine = "";
      for (const w of words) {
        if (!curLine) {
          curLine = w;
        } else if (curLine.length + 1 + w.length <= maxCols) {
          curLine += " " + w;
        } else {
          result.push(curLine);
          curLine = w;
        }
      }
      if (curLine) {
        result.push(curLine);
      }
    };

    for (let i = 1; i < lines.length; i++) {
      const line = lines[i];
      if (isSpecial(line)) {
        flushParagraph();
        result.push(line);
      } else {
        pBuffer.push(line.trim());
      }
    }
    flushParagraph();
    return result.join("\n");
  }

  function wrapPgCommitMsgText(clNum, maxCols = 72) {
    const num = Number(clNum || state.selectedClNumber);
    const textarea = document.getElementById(`commitMsgTextarea-${num}`);
    const originalText = textarea ? textarea.value : ((state.commitMsgDraftByCl && state.commitMsgDraftByCl[num]) || "");
    if (!originalText) return;

    const wrapped = formatWrappedCommitMessage(originalText, maxCols);
    if (textarea) {
      textarea.value = wrapped;
      if (typeof onPgCommitMsgInput === "function") {
        onPgCommitMsgInput(num, textarea);
      }
    }
    if (state.commitMsgDraftByCl) {
      state.commitMsgDraftByCl[num] = wrapped;
    }
    showToast("Wrapped body lines at 72 characters ✓");
  }

  function revertPgCommitMsgText(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const originalMsg = (state.commitMsgOriginalByCl && state.commitMsgOriginalByCl[num]) || "";
    const textarea = document.getElementById(`commitMsgTextarea-${num}`);
    if (textarea) {
      textarea.value = originalMsg;
      onPgCommitMsgInput(num, textarea);
    }
    showToast("Reverted commit message to original ✓");
  }

  function setPgCommitMsgEditorMode(clNum, mode, focusTextarea = true) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number));
    const validModes = ["write", "preview", "split"];
    const targetMode = validModes.includes(mode) ? mode : "write";

    if (!state.commitMsgEditorModeByCl) state.commitMsgEditorModeByCl = {};
    state.commitMsgEditorModeByCl[num] = targetMode;

    const contentEl = document.getElementById(`commitMsgEditorContent-${num}`);
    if (contentEl) {
      contentEl.className = `pg-commit-editor-content mode-${targetMode}`;
    }

    validModes.forEach(m => {
      const btn = document.getElementById(`commitMsgModeBtn-${m}-${num}`);
      if (btn) {
        const isActive = m === targetMode;
        if (isActive) {
          btn.classList.add("is-active");
          btn.setAttribute("aria-selected", "true");
        } else {
          btn.classList.remove("is-active");
          btn.setAttribute("aria-selected", "false");
        }
      }
    });

    if (targetMode === "preview" || targetMode === "split") {
      updatePgCommitMsgPreview(num);
    }

    if (focusTextarea && targetMode !== "preview") {
      const textarea = document.getElementById(`commitMsgTextarea-${num}`);
      if (textarea && typeof textarea.focus === "function") {
        textarea.focus();
      }
    }
  }

  function updatePgCommitMsgPreview(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number));
    const pane = document.getElementById(`commitMsgPreviewPane-${num}`);
    if (!pane) return;

    const textarea = document.getElementById(`commitMsgTextarea-${num}`);
    const text = textarea ? textarea.value : ((state.commitMsgDraftByCl && state.commitMsgDraftByCl[num]) || "");

    if (!String(text || "").trim()) {
      pane.innerHTML = `<div class="commit-md-empty-preview">(No commit message content to preview. Start typing in Write mode.)</div>`;
      return;
    }

    if (typeof formatCommitMessageHtml === "function") {
      pane.innerHTML = formatCommitMessageHtml(text, num);
    }
  }

  function openPgCommitMsgEditor(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number));
    const c = resolvePgChangeForAction(num);
    if (!c) return;

    if (!state.editingCommitMsgByCl) state.editingCommitMsgByCl = {};
    if (!state.commitMsgDraftByCl) state.commitMsgDraftByCl = {};
    if (!state.commitMsgOriginalByCl) state.commitMsgOriginalByCl = {};

    const bundle = state.diffBundleByCl && state.diffBundleByCl[num];
    const originalMsg = (typeof getFullCommitMessage === "function")
      ? getFullCommitMessage(c, bundle)
      : (c.commit_message || (bundle && bundle.commit_message) || c.subject || "");

    state.commitMsgOriginalByCl[num] = originalMsg;
    if (typeof state.commitMsgDraftByCl[num] !== "string" || !state.editingCommitMsgByCl[num]) {
      state.commitMsgDraftByCl[num] = originalMsg;
    }
    state.editingCommitMsgByCl[num] = true;

    if (typeof uncollapseCommitMessage === "function") {
      uncollapseCommitMessage(num, true);
    }

    const panel = document.getElementById(`commitMsgPanel-${num}`);
    if (panel) {
      panel.classList.add("is-editing");
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    const editorEl = document.getElementById(`commitMsgEditor-${num}`);
    if (editorEl) {
      editorEl.style.display = "flex";
    }

    const bodyEl = panel ? (panel.querySelector(".commit-msg-body:not(.pg-commit-preview-pane)") || panel.querySelector(".commit-msg-body")) : null;
    if (bodyEl) {
      bodyEl.style.display = "none";
    }
    const expandWrap = panel ? panel.querySelector(".commit-msg-expand-wrap") : null;
    if (expandWrap) {
      expandWrap.style.display = "none";
    }

    const textarea = document.getElementById(`commitMsgTextarea-${num}`);
    if (textarea) {
      textarea.value = state.commitMsgDraftByCl[num];
      updatePgCommitMsgMetrics(num);
      textarea.focus();
    }

    const activeMode = (state.commitMsgEditorModeByCl && state.commitMsgEditorModeByCl[num]) || "write";
    setPgCommitMsgEditorMode(num, activeMode, false);
    updatePgCommitMsgPreview(num);
  }

  function cancelPgCommitMsgEditor(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const textarea = document.getElementById(`commitMsgTextarea-${num}`);
    const originalMsg = (state.commitMsgOriginalByCl && state.commitMsgOriginalByCl[num]) || "";
    const isDirty = textarea && textarea.value !== originalMsg;

    if (isDirty) {
      if (typeof confirm === "function" && !confirm("You have unsaved changes in the commit message. Discard them?")) {
        return;
      }
    }

    if (state.editingCommitMsgByCl) state.editingCommitMsgByCl[num] = false;
    if (state.commitMsgDraftByCl) state.commitMsgDraftByCl[num] = originalMsg;

    const panel = document.getElementById(`commitMsgPanel-${num}`);
    if (panel) {
      panel.classList.remove("is-editing");
    }
    const editorEl = document.getElementById(`commitMsgEditor-${num}`);
    if (editorEl) {
      editorEl.style.display = "none";
    }
    const bodyEl = panel ? (panel.querySelector(".commit-msg-body:not(.pg-commit-preview-pane)") || panel.querySelector(".commit-msg-body")) : null;
    if (bodyEl) {
      bodyEl.style.display = "";
    }
    const expandWrap = panel ? panel.querySelector(".commit-msg-expand-wrap") : null;
    if (expandWrap) {
      expandWrap.style.display = "";
    }
  }

  function onPgCommitMsgInput(clNum, textarea) {
    const num = Number(clNum);
    if (!textarea) return;
    if (!state.commitMsgDraftByCl) state.commitMsgDraftByCl = {};
    state.commitMsgDraftByCl[num] = textarea.value;

    const originalMsg = (state.commitMsgOriginalByCl && state.commitMsgOriginalByCl[num]) || "";
    const isDirty = textarea.value !== originalMsg;
    const dirtyBadge = document.getElementById(`commitMsgDirtyBadge-${num}`);
    if (dirtyBadge) {
      dirtyBadge.style.display = isDirty ? "inline-flex" : "none";
    }

    updatePgCommitMsgMetrics(num);

    const mode = (state.commitMsgEditorModeByCl && state.commitMsgEditorModeByCl[num]) || "write";
    if (mode === "preview" || mode === "split") {
      updatePgCommitMsgPreview(num);
    }
  }

  function onPgCommitMsgKeyDown(event, clNum) {
    if (!event) return;
    const num = Number(clNum);
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && (event.key === "P" || event.key === "p")) {
      event.preventDefault();
      const curMode = (state.commitMsgEditorModeByCl && state.commitMsgEditorModeByCl[num]) || "write";
      const nextMode = curMode === "write" ? "preview" : (curMode === "preview" ? "split" : "write");
      setPgCommitMsgEditorMode(num, nextMode);
      if (typeof showToast === "function") {
        showToast(`Commit message mode: ${nextMode.charAt(0).toUpperCase() + nextMode.slice(1)}`);
      }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      savePgCommitMsgEditor(num);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancelPgCommitMsgEditor(num);
      return;
    }
  }

  function updatePgCommitMsgMetrics(clNum) {
    const num = Number(clNum);
    const textarea = document.getElementById(`commitMsgTextarea-${num}`);
    const text = textarea ? textarea.value : ((state.commitMsgDraftByCl && state.commitMsgDraftByCl[num]) || "");
    const metricsBar = document.getElementById(`commitMsgMetricsBar-${num}`);
    if (metricsBar && typeof validatePgCommitMessage === "function" && typeof renderPgCommitMsgMetricsHtml === "function") {
      const metrics = validatePgCommitMessage(text);
      metricsBar.innerHTML = renderPgCommitMsgMetricsHtml(metrics);
    }
  }

  async function savePgCommitMsgEditor(clNum) {
    const num = Number(clNum || state.selectedClNumber);
    const c = resolvePgChangeForAction(num);
    if (!c) return;

    const textarea = document.getElementById(`commitMsgTextarea-${num}`);
    const newMsg = textarea ? textarea.value.trim() : ((state.commitMsgDraftByCl && state.commitMsgDraftByCl[num]) || "").trim();
    if (!newMsg) {
      showToast("Commit message cannot be empty.");
      return;
    }

    const saveBtn = document.getElementById(`btnSaveCommitMsg-${num}`);
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";
    }

    try {
      const res = await sidecarFetch("/api/gerrit/edit-commit-message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cl: num, message: newMsg }),
      });
      const data = await res.json();
      if (data && data.status === "error") {
        throw new Error(data.error || "Failed to save commit message.");
      }

      const firstLine = newMsg.split(/\r?\n/)[0].trim();
      c.subject = firstLine;
      c.commit_message = newMsg;
      if (state.diffBundleByCl && state.diffBundleByCl[num]) {
        state.diffBundleByCl[num].commit_message = newMsg;
      }
      if (state.commitMsgDraftByCl) {
        state.commitMsgDraftByCl[num] = newMsg;
      }
      if (state.commitMsgOriginalByCl) {
        state.commitMsgOriginalByCl[num] = newMsg;
      }
      if (state.editingCommitMsgByCl) {
        state.editingCommitMsgByCl[num] = false;
      }

      const subjectEls = document.querySelectorAll(`.pg-change-subject[data-cl="${num}"], .pg-change-title[data-cl="${num}"]`);
      subjectEls.forEach(el => { el.textContent = firstLine; });

      const panel = document.getElementById(`commitMsgPanel-${num}`);
      if (panel) {
        panel.outerHTML = renderCommitMessagePanelHtml(c, state.diffBundleByCl && state.diffBundleByCl[num]);
      }

      showToast("Commit message saved ✓");
    } catch (err) {
      showToast(`Error saving commit message: ${err.message || err}`);
    } finally {
      const btn = document.getElementById(`btnSaveCommitMsg-${num}`);
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Save";
      }
    }
  }

  if (typeof window !== "undefined") {
    window.PG_VALID_COMMIT_TYPES = PG_VALID_COMMIT_TYPES;
    window.validatePgCommitMessage = validatePgCommitMessage;
    window.renderPgCommitMsgMetricsHtml = renderPgCommitMsgMetricsHtml;
    window.formatWrappedCommitMessage = formatWrappedCommitMessage;
    window.wrapPgCommitMsgText = wrapPgCommitMsgText;
    window.revertPgCommitMsgText = revertPgCommitMsgText;
    window.openPgCommitMsgEditor = openPgCommitMsgEditor;
    window.setPgCommitMsgEditorMode = setPgCommitMsgEditorMode;
    window.updatePgCommitMsgPreview = updatePgCommitMsgPreview;
    window.cancelPgCommitMsgEditor = cancelPgCommitMsgEditor;
    window.onPgCommitMsgInput = onPgCommitMsgInput;
    window.onPgCommitMsgKeyDown = onPgCommitMsgKeyDown;
    window.updatePgCommitMsgMetrics = updatePgCommitMsgMetrics;
    window.savePgCommitMsgEditor = savePgCommitMsgEditor;
    window.resolvePgChangeForAction = resolvePgChangeForAction;
    window.computePgDownloadCommands = computePgDownloadCommands;
    window.openPgDownloadDialog = openPgDownloadDialog;
    window.closePgDownloadDialog = closePgDownloadDialog;
    window.setPgDownloadPatchset = setPgDownloadPatchset;
    window.stepPgDownloadPatchset = stepPgDownloadPatchset;
    window.togglePgDownloadBranchOption = togglePgDownloadBranchOption;
    window.onPgDownloadBranchInputChange = onPgDownloadBranchInputChange;
    window.togglePgDownloadRepoBranchOption = togglePgDownloadRepoBranchOption;
    window.selectPgDownloadTab = selectPgDownloadTab;
    window.copyPgDownloadCommand = copyPgDownloadCommand;
    window.copyPgSpecificDownloadCommand = copyPgSpecificDownloadCommand;
    window.copyPgDownloadRef = copyPgDownloadRef;
    window.copyPgPatchUrl = copyPgPatchUrl;
    window.copyPgWorkspaceCommand = copyPgWorkspaceCommand;
    window.downloadPgPatchFile = downloadPgPatchFile;
    window.setPgDownloadScheme = setPgDownloadScheme;
    window.cyclePgDownloadScheme = cyclePgDownloadScheme;
    window.renderPgDownloadAllCommandsHtml = renderPgDownloadAllCommandsHtml;
    window.onPgDownloadBackdropClick = onPgDownloadBackdropClick;
    window.openPgRebaseDialog = openPgRebaseDialog;
    window.getPgChangeDescendants = getPgChangeDescendants;
    window.getPgRebaseCandidateBases = getPgRebaseCandidateBases;
    window.renderPgRebaseSuggestions = renderPgRebaseSuggestions;
    window.selectPgRebaseCustomBase = selectPgRebaseCustomBase;
    window.filterPgRebaseSuggestions = filterPgRebaseSuggestions;
    window.onPgRebaseCustomBaseInput = onPgRebaseCustomBaseInput;
    window.closePgRebaseDialog = closePgRebaseDialog;
    window.onPgRebaseTypeChange = onPgRebaseTypeChange;
    window.onPgRebaseBackdropClick = onPgRebaseBackdropClick;
    window.executePgRebase = executePgRebase;
    window.getPgChangeDescendants = getPgChangeDescendants;
    window.openPgAbandonDialog = openPgAbandonDialog;
    window.closePgAbandonDialog = closePgAbandonDialog;
    window.onPgAbandonBackdropClick = onPgAbandonBackdropClick;
    window.executePgAbandon = executePgAbandon;
    window.openPgRestoreDialog = openPgRestoreDialog;
    window.closePgRestoreDialog = closePgRestoreDialog;
    window.onPgRestoreBackdropClick = onPgRestoreBackdropClick;
    window.executePgRestore = executePgRestore;
    window.openPgRevertDialog = openPgRevertDialog;
    window.closePgRevertDialog = closePgRevertDialog;
    window.onPgRevertBackdropClick = onPgRevertBackdropClick;
    window.executePgRevert = executePgRevert;
    window.openPgSetWipDialog = openPgSetWipDialog;
    window.closePgSetWipDialog = closePgSetWipDialog;
    window.onPgSetWipBackdropClick = onPgSetWipBackdropClick;
    window.executePgSetWip = executePgSetWip;
    window.openPgSetReadyDialog = openPgSetReadyDialog;
    window.closePgSetReadyDialog = closePgSetReadyDialog;
    window.onPgSetReadyBackdropClick = onPgSetReadyBackdropClick;
    window.executePgSetReady = executePgSetReady;
    window.togglePgWorkInProgress = togglePgWorkInProgress;
    window.openPgCherryPickDialog = openPgCherryPickDialog;
    window.closePgCherryPickDialog = closePgCherryPickDialog;
    window.onPgCherryPickBackdropClick = onPgCherryPickBackdropClick;
    window.setPgCherryPickBranch = setPgCherryPickBranch;
    window.executePgCherryPick = executePgCherryPick;
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
    window.openPgFollowUpDialog = openPgFollowUpDialog;
    window.closePgFollowUpDialog = closePgFollowUpDialog;
    window.onPgFollowUpBackdropClick = onPgFollowUpBackdropClick;
    window.setPgFollowUpBranch = setPgFollowUpBranch;
    window.executePgFollowUp = executePgFollowUp;
    window.openPgIncludedInDialog = openPgIncludedInDialog;
    window.closePgIncludedInDialog = closePgIncludedInDialog;
    window.onPgIncludedInBackdropClick = onPgIncludedInBackdropClick;
    window.refreshPgIncludedIn = refreshPgIncludedIn;
    window.onPgIncludedInFilterInput = onPgIncludedInFilterInput;
    window.clearPgIncludedInFilter = clearPgIncludedInFilter;
    window.copyPgIncludedItem = copyPgIncludedItem;
    window.renderPgIncludedInDom = renderPgIncludedInDom;
    window.getActivePgFileDiffBlock = getActivePgFileDiffBlock;
    window.togglePgActiveFileReviewed = togglePgActiveFileReviewed;
    window.togglePgActiveFileDiffCollapse = togglePgActiveFileDiffCollapse;
    window.renderPgHeaderActionButtonsHtml = renderPgHeaderActionButtonsHtml;
    window.getPgChangeReferenceInfo = getPgChangeReferenceInfo;
    window.renderPgCopyLinksMenuHtml = renderPgCopyLinksMenuHtml;
    window.renderPgCopyLinksMenuDom = renderPgCopyLinksMenuDom;
    window.openPgCopyLinksMenu = openPgCopyLinksMenu;
    window.closePgCopyLinksMenu = closePgCopyLinksMenu;
    window.togglePgCopyLinksMenu = togglePgCopyLinksMenu;
    window.copyPgMetaField = copyPgMetaField;
    window.copyPgChangeTitle = copyPgChangeTitle;
    window.openPgFileEditor = openPgFileEditor;
    window.closePgFileEditorDialog = closePgFileEditorDialog;
    window.onPgFileEditorBackdropClick = onPgFileEditorBackdropClick;
    window.onPgFileEditClick = onPgFileEditClick;
    window.onPgEditorInput = onPgEditorInput;
    window.onPgEditorScroll = onPgEditorScroll;
    window.onPgEditorCursorMove = onPgEditorCursorMove;
    window.updatePgEditorGutter = updatePgEditorGutter;
    window.updatePgEditorMetrics = updatePgEditorMetrics;
    window.togglePgEditorWrap = togglePgEditorWrap;
    window.cyclePgEditorTabSize = cyclePgEditorTabSize;
    window.onPgEditorKeyDown = onPgEditorKeyDown;
    window.saveActiveFileEdit = saveActiveFileEdit;
    window.saveAndPublishActiveFileEdit = saveAndPublishActiveFileEdit;
    window.publishActiveChangeEdit = publishActiveChangeEdit;
    window.discardActiveChangeEdit = discardActiveChangeEdit;
    window.updatePgHeaderActionsDom = updatePgHeaderActionsDom;
  }

