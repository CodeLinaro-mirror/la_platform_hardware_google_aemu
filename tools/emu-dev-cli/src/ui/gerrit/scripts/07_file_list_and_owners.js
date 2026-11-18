  function getPgFileDir(path) {
    const p = String(path || "");
    const lastSlash = p.lastIndexOf("/");
    return lastSlash !== -1 ? p.substring(0, lastSlash + 1) : "";
  }

  function computePgClOwnerStatusPrefix(c) {
    if (!c) return "0|0|0||";
    const clNum = c.number || 0;
    const cr2Len = Array.isArray(c.human_cr2) ? c.human_cr2.length : 0;
    const revs = Array.isArray(c.reviewers_detail) ? c.reviewers_detail : [];
    const revsLen = revs.length;
    const r0 = revsLen > 0 ? revs[0] : null;
    const rLast = revsLen > 1 ? revs[revsLen - 1] : r0;
    const srs = Array.isArray(c.submit_requirements) ? c.submit_requirements : [];
    const srsLen = srs.length;
    const sr0 = srsLen > 0 ? srs[0] : null;
    const srLast = srsLen > 1 ? srs[srsLen - 1] : sr0;
    if (
      typeof c._cachedOwnerStatusPrefix === "string" &&
      c._ownerPrefixClNum === clNum &&
      c._ownerPrefixSub === c.is_submittable &&
      c._ownerPrefixCr2Cnt === c.human_cr2_count &&
      c._ownerPrefixViewerCr === c.viewer_cr_vote &&
      c._ownerPrefixCr2Len === cr2Len &&
      c._ownerPrefixRevsRef === revs &&
      c._ownerPrefixRevsLen === revsLen &&
      c._ownerPrefixR0Vote === (r0 ? (r0.cr_vote ?? r0.code_review_vote ?? 0) : undefined) &&
      c._ownerPrefixRLastVote === (rLast ? (rLast.cr_vote ?? rLast.code_review_vote ?? 0) : undefined) &&
      c._ownerPrefixSrsRef === srs &&
      c._ownerPrefixSrsLen === srsLen &&
      c._ownerPrefixSr0St === (sr0 ? sr0.status : undefined) &&
      c._ownerPrefixSrLastSt === (srLast ? srLast.status : undefined)
    ) {
      return c._cachedOwnerStatusPrefix;
    }
    let revSig = "";
    for (let i = 0; i < revsLen; i++) {
      const r = revs[i];
      if (r) revSig += `${r.email || ""}:${r.cr_vote ?? r.code_review_vote ?? 0};`;
    }
    let srSig = "";
    for (let i = 0; i < srsLen; i++) {
      const sr = srs[i];
      if (sr && (sr.name === "Code-Owners" || sr.name === "find-owners")) {
        srSig += `${sr.name}:${sr.status || ""};`;
      }
    }
    const prefix = `${clNum}|${c.is_submittable ? 1 : 0}|${c.human_cr2_count || 0}|${c.viewer_cr_vote || 0}|${cr2Len}|${revSig}|${srSig}`;
    c._ownerPrefixClNum = clNum;
    c._ownerPrefixSub = c.is_submittable;
    c._ownerPrefixCr2Cnt = c.human_cr2_count;
    c._ownerPrefixViewerCr = c.viewer_cr_vote;
    c._ownerPrefixCr2Len = cr2Len;
    c._ownerPrefixRevsRef = revs;
    c._ownerPrefixRevsLen = revsLen;
    c._ownerPrefixR0Vote = r0 ? (r0.cr_vote ?? r0.code_review_vote ?? 0) : undefined;
    c._ownerPrefixRLastVote = rLast ? (rLast.cr_vote ?? rLast.code_review_vote ?? 0) : undefined;
    c._ownerPrefixSrsRef = srs;
    c._ownerPrefixSrsLen = srsLen;
    c._ownerPrefixSr0St = sr0 ? sr0.status : undefined;
    c._ownerPrefixSrLastSt = srLast ? srLast.status : undefined;
    c._cachedOwnerStatusPrefix = prefix;
    return prefix;
  }

  function computePgFileOwnerStatusCacheKey(c, f, clPrefixOpt) {
    const clPrefix = clPrefixOpt !== undefined ? clPrefixOpt : computePgClOwnerStatusPrefix(c);
    const ownersLen = Array.isArray(f.owners) ? f.owners.length : 0;
    return `${clPrefix}|${f.file || ""}|${f.owner_status || ""}|${f.owner_approved ?? ""}|${f.owner_detail || ""}|${f.owner_label || ""}|${f.owner_file || ""}|${f.owner_rule || ""}|${ownersLen}`;
  }

  const _NA_FILE_OWNER_STATUS = Object.freeze({
    status: "na",
    label: "—",
    title: "Not applicable",
    owner_file: "",
    owner_rule: "",
    owners: [],
    approvers: [],
    missing_approvals: 0
  });

  function getPgClOwnerStatusBase(c, clPrefix) {
    if (c && c._cachedOwnerBaseKey === clPrefix && c._cachedOwnerBase) {
      return c._cachedOwnerBase;
    }
    const humanCr2 = (c && Array.isArray(c.human_cr2)) ? c.human_cr2 : [];
    const reviewers = (typeof getFallbackReviewersForCl === "function" && c) ? getFallbackReviewersForCl(c) : [];
    const approverEmails = new Set();
    for (let i = 0; i < humanCr2.length; i++) {
      const em = (humanCr2[i].email || "").toLowerCase();
      if (em) approverEmails.add(em);
    }
    const eligibleOwners = [];
    const seen = new Set();
    for (let i = 0; i < humanCr2.length; i++) {
      const h = humanCr2[i];
      const em = (h.email || "").toLowerCase();
      if (em && !seen.has(em)) {
        seen.add(em);
        eligibleOwners.push({
          name: h.name || h.short || em.split("@")[0],
          email: em,
          approved: true,
          is_reviewer: true
        });
      }
    }
    for (let i = 0; i < reviewers.length; i++) {
      const r = reviewers[i];
      const em = (r.email || "").toLowerCase();
      if (em && !seen.has(em) && !r.is_bot) {
        seen.add(em);
        eligibleOwners.push({
          name: r.name || r.short || em.split("@")[0],
          email: em,
          approved: Number(r.cr_vote || 0) >= 2,
          is_reviewer: true
        });
      }
    }
    if (!eligibleOwners.length && c && c.owner_email && !c.owner_is_bot) {
      eligibleOwners.push({
        name: c.owner_name || c.owner_short || "Owner",
        email: c.owner_email,
        approved: false,
        is_reviewer: true
      });
    }
    const approvers = eligibleOwners.filter(o => o.approved);
    const srs = (c && Array.isArray(c.submit_requirements)) ? c.submit_requirements : [];
    const codeOwnersSr = srs.find(sr => sr && (sr.name === "Code-Owners" || sr.name === "find-owners"));
    let defaultStatus = "missing";
    let defaultLabel = "Missing approval";
    let defaultTitle = "Pending code owner approval (+2)";
    let defaultMissing = 1;
    let srHandled = false;
    if (codeOwnersSr) {
      if (codeOwnersSr.status === "SATISFIED" || codeOwnersSr.status === "NOT_APPLICABLE") {
        defaultStatus = "approved";
        defaultLabel = "Owner approved";
        defaultTitle = "Approved by code owner";
        defaultMissing = 0;
        srHandled = true;
      } else if (codeOwnersSr.status === "UNSATISFIED" || codeOwnersSr.status === "BLOCKED") {
        defaultStatus = "missing";
        defaultLabel = "Missing approval";
        defaultTitle = "Missing code owner approval";
        defaultMissing = 1;
        srHandled = true;
      }
    }
    if (!srHandled && (humanCr2.length > 0 || (c && c.is_submittable))) {
      const approverNames = humanCr2.map(h => h.name || h.short || "reviewer").join(", ");
      defaultStatus = "approved";
      defaultLabel = "Owner approved";
      defaultTitle = approverNames ? `Approved by code owner (${approverNames})` : "Approved by code owner";
      defaultMissing = 0;
    }
    const safeApproversStr = escapeHtml(approvers.map(a => a.name || a.email).filter(Boolean).join(", "));
    const safeOwnersStr = escapeHtml(eligibleOwners.map(a => a.name || a.email).filter(Boolean).join(", "));
    const safeDefaultTitle = escapeHtml(defaultTitle);
    const safeDefaultLabel = escapeHtml(defaultLabel);
    const base = {
      humanCr2,
      reviewers,
      approverEmails,
      eligibleOwners,
      approvers,
      defaultStatus,
      defaultLabel,
      defaultTitle,
      defaultMissing,
      safeApproversStr,
      safeOwnersStr,
      safeDefaultTitle,
      safeDefaultLabel,
      _dirSimpleMap: Object.create(null),
    };
    if (c) {
      c._cachedOwnerBaseKey = clPrefix;
      c._cachedOwnerBase = base;
    }
    return base;
  }

  function getPgFileOwnerStatus(c, f, clPrefixOpt) {
    if (!f || f.file === "/COMMIT_MSG" || f.file === "/MERGE_LIST" || (typeof GERRIT_MAGIC_FILES !== "undefined" && GERRIT_MAGIC_FILES.has(f.file))) {
      return _NA_FILE_OWNER_STATUS;
    }
    const clPrefix = clPrefixOpt !== undefined ? clPrefixOpt : computePgClOwnerStatusPrefix(c);
    const isSimpleFile = (
      !f.owner_status &&
      f.owner_approved === undefined &&
      !f.owner_detail &&
      !f.owner_label &&
      !f.owner_file &&
      !f.owner_rule &&
      (!Array.isArray(f.owners) || f.owners.length === 0)
    );
    if (isSimpleFile) {
      if (f._cachedOwnerSimpleClPrefix === clPrefix && f._cachedOwnerSimpleFile === f.file && f._cachedOwnerStatus) {
        return f._cachedOwnerStatus;
      }
      const base = getPgClOwnerStatusBase(c, clPrefix);
      const filePath = String(f.file || "");
      const lastSlash = filePath.lastIndexOf("/");
      const dir = lastSlash > 0 ? filePath.slice(0, lastSlash) : "";
      let res = base._dirSimpleMap && base._dirSimpleMap[dir];
      if (!res) {
        const candidateOwnerFile = dir ? `//${dir}/OWNERS` : "//OWNERS";
        const candidateOwnerRule = "* (directory default)";
        res = {
          status: base.defaultStatus,
          label: base.defaultLabel,
          title: base.defaultTitle,
          owner_file: candidateOwnerFile,
          owner_rule: candidateOwnerRule,
          owners: base.eligibleOwners,
          approvers: base.approvers,
          missing_approvals: base.defaultMissing,
          _safeApproversStr: base.safeApproversStr,
          _safeOwnersStr: base.safeOwnersStr,
          _safeTitle: base.safeDefaultTitle,
          _safeLabel: base.safeDefaultLabel,
          _safeOwnerFile: escapeHtml(candidateOwnerFile),
          _safeOwnerRule: candidateOwnerRule,
        };
        if (base._dirSimpleMap) {
          base._dirSimpleMap[dir] = res;
        }
      }
      f._cachedOwnerSimpleClPrefix = clPrefix;
      f._cachedOwnerSimpleFile = f.file;
      f._cachedOwnerStatus = res;
      return res;
    }
    const oSub = f._ownerStatSub;
    const ownersArr = Array.isArray(f.owners) ? f.owners : null;
    const ownersLen = ownersArr ? ownersArr.length : 0;
    const owner0 = ownersLen > 0 ? ownersArr[0] : null;
    const ownerLast = ownersLen > 1 ? ownersArr[ownersLen - 1] : owner0;
    if (
      oSub &&
      f._cachedOwnerStatus &&
      oSub.clPrefix === clPrefix &&
      oSub.file === f.file &&
      oSub.st === f.owner_status &&
      oSub.app === f.owner_approved &&
      oSub.det === f.owner_detail &&
      oSub.lbl === f.owner_label &&
      oSub.oFile === f.owner_file &&
      oSub.oRule === f.owner_rule &&
      oSub.oRef === ownersArr &&
      oSub.oLen === ownersLen &&
      oSub.o0 === owner0 &&
      oSub.oLast === ownerLast
    ) {
      return f._cachedOwnerStatus;
    }
    const cacheKey = computePgFileOwnerStatusCacheKey(c, f, clPrefix);
    if (f._cachedOwnerStatusKey === cacheKey && f._cachedOwnerStatus) {
      f._ownerStatSub = { clPrefix, file: f.file, st: f.owner_status, app: f.owner_approved, det: f.owner_detail, lbl: f.owner_label, oFile: f.owner_file, oRule: f.owner_rule, oRef: ownersArr, oLen: ownersLen, o0: owner0, oLast: ownerLast };
      return f._cachedOwnerStatus;
    }
    const base = getPgClOwnerStatusBase(c, clPrefix);
    const res = computePgFileOwnerStatusUncached(c, f, clPrefix, base);
    res._safeApproversStr = res.approvers === base.approvers
      ? base.safeApproversStr
      : escapeHtml(Array.isArray(res.approvers) ? res.approvers.map(a => a.name || a.email).filter(Boolean).join(", ") : "");
    res._safeOwnersStr = res.owners === base.eligibleOwners
      ? base.safeOwnersStr
      : escapeHtml(Array.isArray(res.owners) ? res.owners.map(a => a.name || a.email).filter(Boolean).join(", ") : "");
    const rawTitle = res.title || "";
    res._safeTitle = rawTitle === base.defaultTitle
      ? base.safeDefaultTitle
      : (rawTitle === "Approved by code owner" || rawTitle === "Missing code owner approval" ? rawTitle : escapeHtml(rawTitle));
    const rawLabel = res.label || (res.status === "approved" ? "Owner approved" : "Missing approval");
    res._safeLabel = (rawLabel === "Owner approved" || rawLabel === "Missing approval") ? rawLabel : escapeHtml(rawLabel);
    res._safeOwnerFile = escapeHtml(res.owner_file || "");
    const rawRule = res.owner_rule || "";
    res._safeOwnerRule = rawRule === "* (directory default)" ? rawRule : escapeHtml(rawRule);
    f._cachedOwnerSimpleClPrefix = "";
    f._cachedOwnerStatusKey = cacheKey;
    f._cachedOwnerStatus = res;
    f._ownerStatSub = { clPrefix, file: f.file, st: f.owner_status, app: f.owner_approved, det: f.owner_detail, lbl: f.owner_label, oFile: f.owner_file, oRule: f.owner_rule, oRef: ownersArr, oLen: ownersLen, o0: owner0, oLast: ownerLast };
    return res;
  }

  function computePgFileOwnerStatusUncached(c, f, clPrefixOpt, baseOpt) {
    // Determine candidate OWNERS file & rule
    const filePath = String(f.file || "");
    const lastSlash = filePath.lastIndexOf("/");
    const dir = lastSlash > 0 ? filePath.slice(0, lastSlash) : "";
    const candidateOwnerFile = f.owner_file || (dir ? `//${dir}/OWNERS` : "//OWNERS");
    const candidateOwnerRule = f.owner_rule || "* (directory default)";

    const clPrefix = clPrefixOpt !== undefined ? clPrefixOpt : computePgClOwnerStatusPrefix(c);
    const base = baseOpt || getPgClOwnerStatusBase(c, clPrefix);
    const { humanCr2, reviewers, approverEmails } = base;

    // Resolve eligible owners list
    let eligibleOwners = base.eligibleOwners;
    let eligibleApprovers = base.approvers;
    if (Array.isArray(f.owners) && f.owners.length > 0) {
      eligibleOwners = f.owners.map(o => {
        if (typeof o === "string") {
          const em = o.trim().toLowerCase();
          const isAppr = approverEmails.has(em);
          const isRev = reviewers.some(r => (r.email || "").toLowerCase() === em);
          const name = (reviewers.find(r => (r.email || "").toLowerCase() === em) || {}).name || em.split("@")[0];
          return { name, email: em, approved: isAppr, is_reviewer: isRev };
        }
        const em = String(o.email || "").trim().toLowerCase();
        const isAppr = Boolean(o.approved || approverEmails.has(em));
        const isRev = Boolean(o.is_reviewer || reviewers.some(r => (r.email || "").toLowerCase() === em));
        return {
          name: o.name || em.split("@")[0] || "Code Owner",
          email: em,
          approved: isAppr,
          is_reviewer: isRev
        };
      });
      eligibleApprovers = eligibleOwners.filter(o => o.approved);
    }

    // 1. Explicit per-file metadata if provided by backend or test
    if (f.owner_status) {
      const st = String(f.owner_status).toLowerCase();
      if (st === "approved") {
        return {
          status: "approved",
          label: "Owner approved",
          title: f.owner_detail || "Approved by code owner",
          owner_file: candidateOwnerFile,
          owner_rule: candidateOwnerRule,
          owners: eligibleOwners,
          approvers: eligibleApprovers,
          missing_approvals: 0
        };
      }
      if (st === "missing") {
        return {
          status: "missing",
          label: "Missing approval",
          title: f.owner_detail || "Missing code owner approval",
          owner_file: candidateOwnerFile,
          owner_rule: candidateOwnerRule,
          owners: eligibleOwners,
          approvers: eligibleApprovers,
          missing_approvals: 1
        };
      }
      return {
        status: st,
        label: f.owner_label || st,
        title: f.owner_detail || "",
        owner_file: candidateOwnerFile,
        owner_rule: candidateOwnerRule,
        owners: eligibleOwners,
        approvers: eligibleApprovers,
        missing_approvals: st === "approved" ? 0 : 1
      };
    }
    if (f.owner_approved === true) {
      return {
        status: "approved",
        label: "Owner approved",
        title: "Approved by code owner",
        owner_file: candidateOwnerFile,
        owner_rule: candidateOwnerRule,
        owners: eligibleOwners,
        approvers: eligibleApprovers,
        missing_approvals: 0
      };
    }
    if (f.owner_approved === false) {
      return {
        status: "missing",
        label: "Missing approval",
        title: "Missing code owner approval",
        owner_file: candidateOwnerFile,
        owner_rule: candidateOwnerRule,
        owners: eligibleOwners,
        approvers: eligibleApprovers,
        missing_approvals: 1
      };
    }

    return {
      status: base.defaultStatus,
      label: base.defaultLabel,
      title: base.defaultTitle,
      owner_file: candidateOwnerFile,
      owner_rule: candidateOwnerRule,
      owners: eligibleOwners,
      approvers: eligibleApprovers,
      missing_approvals: base.defaultMissing
    };
  }

  let pgOwnerTooltipHideTimer = null;

  function renderPgOwnerTooltipContentHtml(data) {
    if (!data) return "";
    const isApproved = data.status === "approved";
    const statusClass = isApproved ? "is-approved" : "is-missing";
    const icon = isApproved ? "✓" : "✕";
    const statusText = isApproved ? "Owner approved" : "Missing code owner approval";
    const isDir = Boolean(data.isDir);

    let rowsHtml = "";
    if (data.target) {
      rowsHtml += `<div class="pg-owner-tooltip-row">
        <span class="pg-owner-tooltip-label">${isDir ? "Directory" : "File"}:</span>
        <span class="pg-owner-tooltip-val is-mono">${escapeHtml(data.target)}</span>
      </div>`;
    }
    if (data.ownerFile) {
      rowsHtml += `<div class="pg-owner-tooltip-row">
        <span class="pg-owner-tooltip-label">OWNERS:</span>
        <span class="pg-owner-tooltip-val is-mono">${escapeHtml(data.ownerFile)}</span>
      </div>`;
    }
    if (data.ownerRule && data.ownerRule !== "* (directory default)") {
      rowsHtml += `<div class="pg-owner-tooltip-row">
        <span class="pg-owner-tooltip-label">Rule:</span>
        <span class="pg-owner-tooltip-val is-mono">${escapeHtml(data.ownerRule)}</span>
      </div>`;
    }
    if (data.approvers) {
      rowsHtml += `<div class="pg-owner-tooltip-row">
        <span class="pg-owner-tooltip-label">Approved:</span>
        <span class="pg-owner-tooltip-val">${escapeHtml(data.approvers)} (+2)</span>
      </div>`;
    }
    if (!isApproved) {
      const missingCount = Number(data.missing || 1);
      rowsHtml += `<div class="pg-owner-tooltip-row">
        <span class="pg-owner-tooltip-label">Required:</span>
        <span class="pg-owner-tooltip-val">${missingCount === 1 ? "1 code owner approval required" : `${missingCount} code owner approvals required`}</span>
      </div>`;
      if (data.owners) {
        rowsHtml += `<div class="pg-owner-tooltip-row">
          <span class="pg-owner-tooltip-label">Owners:</span>
          <span class="pg-owner-tooltip-val">${escapeHtml(data.owners)}</span>
        </div>`;
      }
    }

    return `<div class="pg-owner-tooltip-header ${statusClass}">
      <span class="pg-owner-tooltip-icon">${icon}</span>
      <span class="pg-owner-tooltip-status-text">${escapeHtml(data.title || statusText)}</span>
    </div>
    <div class="pg-owner-tooltip-body">
      ${rowsHtml}
    </div>
    <div class="pg-owner-tooltip-hint">Click badge to view rule hierarchy &amp; add reviewers</div>`;
  }

  function ensurePgOwnerTooltipEl() {
    let el = document.getElementById("pgOwnerTooltip");
    if (!el && typeof document !== "undefined" && typeof document.createElement === "function") {
      el = document.createElement("div");
      el.id = "pgOwnerTooltip";
      el.className = "pg-owner-tooltip";
      el.setAttribute("role", "tooltip");
      el.setAttribute("aria-hidden", "true");
      el.style.display = "none";
      el.onmouseenter = cancelHidePgOwnerTooltip;
      el.onmouseleave = scheduleHidePgOwnerTooltip;
      if (document.body && typeof document.body.appendChild === "function") {
        document.body.appendChild(el);
      }
    }
    return el;
  }

  function positionPgOwnerTooltip(anchorEl, tooltipEl) {
    if (!anchorEl || !tooltipEl) return;
    const rect = (typeof anchorEl.getBoundingClientRect === "function") ? anchorEl.getBoundingClientRect() : { top: 100, bottom: 120, left: 100, width: 80, height: 20 };
    const ttRect = (typeof tooltipEl.getBoundingClientRect === "function") ? tooltipEl.getBoundingClientRect() : { width: 280, height: 110 };
    const ttWidth = ttRect.width || 280;
    const ttHeight = ttRect.height || 110;

    let top = (rect.top || 0) - ttHeight - 8;
    let placeBelow = false;
    if (top < 10) {
      top = (rect.bottom || 0) + 8;
      placeBelow = true;
    }

    const winW = (typeof window !== "undefined" && window.innerWidth) || 1200;
    let left = (rect.left || 0) + ((rect.width || 0) / 2) - (ttWidth / 2);
    left = Math.max(10, Math.min(left, winW - ttWidth - 10));

    if (tooltipEl.style) {
      tooltipEl.style.top = `${Math.round(top)}px`;
      tooltipEl.style.left = `${Math.round(left)}px`;
    }
    if (tooltipEl.classList) {
      tooltipEl.classList.toggle("place-below", placeBelow);
      tooltipEl.classList.toggle("place-above", !placeBelow);
    }
  }

  function showPgOwnerTooltip(anchorEl, data = null) {
    if (!anchorEl) return;
    cancelHidePgOwnerTooltip();
    const tooltipEl = ensurePgOwnerTooltipEl();
    if (!tooltipEl) return;

    const d = data || {
      target: anchorEl.getAttribute("data-owner-target") || "",
      isDir: anchorEl.getAttribute("data-owner-is-dir") === "1",
      status: anchorEl.getAttribute("data-owner-status") || "approved",
      label: anchorEl.getAttribute("data-owner-label") || "",
      title: anchorEl.getAttribute("data-owner-title") || "",
      ownerFile: anchorEl.getAttribute("data-owner-file") || "",
      ownerRule: anchorEl.getAttribute("data-owner-rule") || "",
      approvers: anchorEl.getAttribute("data-owner-approvers") || "",
      owners: anchorEl.getAttribute("data-owner-owners") || "",
      missing: Number(anchorEl.getAttribute("data-owner-missing") || 0),
    };

    if (!d.target && !d.title) return;

    tooltipEl.innerHTML = renderPgOwnerTooltipContentHtml(d);
    if (tooltipEl.style) tooltipEl.style.display = "block";
    tooltipEl.setAttribute("aria-hidden", "false");
    anchorEl.setAttribute("aria-describedby", "pgOwnerTooltip");

    positionPgOwnerTooltip(anchorEl, tooltipEl);
    if (tooltipEl.classList) tooltipEl.classList.add("is-visible");
  }

  function hidePgOwnerTooltip() {
    cancelHidePgOwnerTooltip();
    const tooltipEl = document.getElementById("pgOwnerTooltip");
    if (tooltipEl) {
      if (tooltipEl.classList) tooltipEl.classList.remove("is-visible");
      tooltipEl.setAttribute("aria-hidden", "true");
      if (tooltipEl.style) tooltipEl.style.display = "none";
    }
    if (typeof document !== "undefined" && typeof document.querySelector === "function") {
      const activeBadge = document.querySelector('.pg-owner-badge[aria-describedby="pgOwnerTooltip"]');
      if (activeBadge && typeof activeBadge.removeAttribute === "function") {
        activeBadge.removeAttribute("aria-describedby");
      }
    }
  }

  function scheduleHidePgOwnerTooltip() {
    cancelHidePgOwnerTooltip();
    pgOwnerTooltipHideTimer = setTimeout(() => {
      hidePgOwnerTooltip();
    }, 180);
  }

  function cancelHidePgOwnerTooltip() {
    if (pgOwnerTooltipHideTimer) {
      clearTimeout(pgOwnerTooltipHideTimer);
      pgOwnerTooltipHideTimer = null;
    }
  }

  function onPgOwnerBadgeMouseEnter(event, badgeEl) {
    if (typeof isPgActivelyScrolling === "function" && isPgActivelyScrolling(event)) return;
    const el = badgeEl || (event && (event.currentTarget || event.target));
    if (el) showPgOwnerTooltip(el);
  }

  function onPgOwnerBadgeMouseLeave(_event, _badgeEl) {
    scheduleHidePgOwnerTooltip();
  }

  function onPgOwnerBadgeFocus(event, badgeEl) {
    const el = badgeEl || (event && (event.currentTarget || event.target));
    if (el) showPgOwnerTooltip(el);
  }

  function onPgOwnerBadgeBlur(_event, _badgeEl) {
    hidePgOwnerTooltip();
  }

  const _NA_FILE_OWNER_BADGE_HTML = '<span class="pg-owner-badge status-na" title="Not applicable">—</span>';

  function renderPgFileOwnerBadgeHtml(ownerInfo, clNum = null, filePath = null, isDir = false, dirName = "", isTargetSafe = false) {
    if (!ownerInfo || ownerInfo.status === "na") {
      const naTitle = (ownerInfo && ownerInfo.title) || "Not applicable";
      if (naTitle === "Not applicable") return _NA_FILE_OWNER_BADGE_HTML;
      return `<span class="pg-owner-badge status-na" title="${escapeHtml(naTitle)}">—</span>`;
    }
    const num = Number(clNum || (typeof state !== "undefined" && state.selectedClNumber) || 0);
    const rawTarget = isDir ? (dirName || filePath || "") : (filePath || "");
    if (
      !isDir &&
      ownerInfo._cachedBadgeCl === num &&
      ownerInfo._cachedBadgeRawTarget === rawTarget &&
      ownerInfo._cachedBadgeStatus === ownerInfo.status &&
      typeof ownerInfo._cachedBadgeHtml === "string"
    ) {
      return ownerInfo._cachedBadgeHtml;
    }
    const target = isTargetSafe ? String(rawTarget) : escapeHtml(rawTarget);
    if (!isDir && num && target) {
      if (
        ownerInfo._badgePartsCl !== num ||
        ownerInfo._badgePartsStatus !== ownerInfo.status ||
        ownerInfo._badgePartsTitle !== ownerInfo.title ||
        ownerInfo._badgePartsLabel !== ownerInfo.label ||
        ownerInfo._badgePartsOwnerFile !== ownerInfo.owner_file ||
        ownerInfo._badgePartsOwnerRule !== ownerInfo.owner_rule ||
        ownerInfo._badgePartsMissing !== ownerInfo.missing_approvals
      ) {
        const isApproved = ownerInfo.status === "approved";
        const cls = isApproved ? "status-approved" : "status-missing";
        const icon = isApproved ? "✓" : "✕";
        const safeTitle = ownerInfo._safeTitle !== undefined ? ownerInfo._safeTitle : escapeHtml(ownerInfo.title || "");
        const safeLabel = ownerInfo._safeLabel !== undefined
          ? ownerInfo._safeLabel
          : escapeHtml(ownerInfo.label || (isApproved ? "Owner approved" : "Missing approval"));
        const safeApproversStr = ownerInfo._safeApproversStr !== undefined
          ? ownerInfo._safeApproversStr
          : escapeHtml(Array.isArray(ownerInfo.approvers) ? ownerInfo.approvers.map(a => a.name || a.email).filter(Boolean).join(", ") : "");
        const safeOwnersStr = ownerInfo._safeOwnersStr !== undefined
          ? ownerInfo._safeOwnersStr
          : escapeHtml(Array.isArray(ownerInfo.owners) ? ownerInfo.owners.map(a => a.name || a.email).filter(Boolean).join(", ") : "");
        const safeOwnerFile = ownerInfo._safeOwnerFile !== undefined ? ownerInfo._safeOwnerFile : escapeHtml(ownerInfo.owner_file || "");
        const safeOwnerRule = ownerInfo._safeOwnerRule !== undefined ? ownerInfo._safeOwnerRule : escapeHtml(ownerInfo.owner_rule || "");
        const missingNum = Number(ownerInfo.missing_approvals !== undefined ? ownerInfo.missing_approvals : (isApproved ? 0 : 1));
        const safeStatus = (ownerInfo.status === "approved" || ownerInfo.status === "missing") ? ownerInfo.status : escapeHtml(ownerInfo.status || "");
        ownerInfo._badgePartsCl = num;
        ownerInfo._badgePartsStatus = ownerInfo.status;
        ownerInfo._badgePartsTitle = ownerInfo.title;
        ownerInfo._badgePartsLabel = ownerInfo.label;
        ownerInfo._badgePartsOwnerFile = ownerInfo.owner_file;
        ownerInfo._badgePartsOwnerRule = ownerInfo.owner_rule;
        ownerInfo._badgePartsMissing = ownerInfo.missing_approvals;
        ownerInfo._badgeP0 = `<span class="pg-owner-badge ${cls} is-clickable" role="button" tabindex="0" onclick="onPgFileOwnerBadgeClick(event, ${num}, '`;
        ownerInfo._badgeP1 = `')" onkeydown="onPgFileOwnerBadgeKeyDown(event, ${num}, '`;
        ownerInfo._badgeP2 = `')" data-owner-cl="${num}" data-owner-target="`;
        ownerInfo._badgeP3 = `" data-owner-is-dir="0" data-owner-status="${safeStatus}" data-owner-label="${safeLabel}" data-owner-title="${safeTitle}" data-owner-file="${safeOwnerFile}" data-owner-rule="${safeOwnerRule}" data-owner-approvers="${safeApproversStr}" data-owner-owners="${safeOwnersStr}" data-owner-missing="${missingNum}" onmouseenter="onPgOwnerBadgeMouseEnter(event, this)" onmouseleave="onPgOwnerBadgeMouseLeave(event, this)" onfocus="onPgOwnerBadgeFocus(event, this)" onblur="onPgOwnerBadgeBlur(event, this)" title="Code Owners: ${safeTitle} · Click for rule breakdown & approvers" aria-label="Code Owners: ${safeLabel}">
      <span class="pg-owner-badge-icon">${icon}</span>
      <span class="pg-owner-badge-text">${safeLabel}</span>
    </span>`;
      }
      const html = ownerInfo._badgeP0 + target + ownerInfo._badgeP1 + target + ownerInfo._badgeP2 + target + ownerInfo._badgeP3;
      ownerInfo._cachedBadgeCl = num;
      ownerInfo._cachedBadgeRawTarget = rawTarget;
      ownerInfo._cachedBadgeStatus = ownerInfo.status;
      ownerInfo._cachedBadgeHtml = html;
      return html;
    }
    const isApproved = ownerInfo.status === "approved";
    const cls = isApproved ? "status-approved" : "status-missing";
    const icon = isApproved ? "✓" : "✕";
    const safeTitle = ownerInfo._safeTitle !== undefined ? ownerInfo._safeTitle : escapeHtml(ownerInfo.title || "");
    const safeLabel = ownerInfo._safeLabel !== undefined
      ? ownerInfo._safeLabel
      : escapeHtml(ownerInfo.label || (isApproved ? "Owner approved" : "Missing approval"));
    const clickAttr = num && target
      ? (isDir
          ? ` onclick="onPgDirOwnerBadgeClick(event, ${num}, '${target}')" onkeydown="onPgDirOwnerBadgeKeyDown(event, ${num}, '${target}')"`
          : ` onclick="onPgFileOwnerBadgeClick(event, ${num}, '${target}')" onkeydown="onPgFileOwnerBadgeKeyDown(event, ${num}, '${target}')"`)
      : "";
    const roleAttr = clickAttr ? ` role="button" tabindex="0"` : "";
    const clickableCls = clickAttr ? " is-clickable" : "";

    const safeApproversStr = ownerInfo._safeApproversStr !== undefined
      ? ownerInfo._safeApproversStr
      : escapeHtml(Array.isArray(ownerInfo.approvers) ? ownerInfo.approvers.map(a => a.name || a.email).filter(Boolean).join(", ") : "");
    const safeOwnersStr = ownerInfo._safeOwnersStr !== undefined
      ? ownerInfo._safeOwnersStr
      : escapeHtml(Array.isArray(ownerInfo.owners) ? ownerInfo.owners.map(a => a.name || a.email).filter(Boolean).join(", ") : "");
    const safeOwnerFile = ownerInfo._safeOwnerFile !== undefined ? ownerInfo._safeOwnerFile : escapeHtml(ownerInfo.owner_file || "");
    const safeOwnerRule = ownerInfo._safeOwnerRule !== undefined ? ownerInfo._safeOwnerRule : escapeHtml(ownerInfo.owner_rule || "");
    const missingNum = Number(ownerInfo.missing_approvals !== undefined ? ownerInfo.missing_approvals : (isApproved ? 0 : 1));
    const safeStatus = (ownerInfo.status === "approved" || ownerInfo.status === "missing") ? ownerInfo.status : escapeHtml(ownerInfo.status || "");

    const dataAttrs = num && target
      ? ` data-owner-cl="${num}" data-owner-target="${target}" data-owner-is-dir="${isDir ? "1" : "0"}" data-owner-status="${safeStatus}" data-owner-label="${safeLabel}" data-owner-title="${safeTitle}" data-owner-file="${safeOwnerFile}" data-owner-rule="${safeOwnerRule}" data-owner-approvers="${safeApproversStr}" data-owner-owners="${safeOwnersStr}" data-owner-missing="${missingNum}" onmouseenter="onPgOwnerBadgeMouseEnter(event, this)" onmouseleave="onPgOwnerBadgeMouseLeave(event, this)" onfocus="onPgOwnerBadgeFocus(event, this)" onblur="onPgOwnerBadgeBlur(event, this)"`
      : "";

    const html = `<span class="pg-owner-badge ${cls}${clickableCls}"${roleAttr}${clickAttr}${dataAttrs} title="Code Owners: ${safeTitle} · Click for rule breakdown & approvers" aria-label="Code Owners: ${safeLabel}">
      <span class="pg-owner-badge-icon">${icon}</span>
      <span class="pg-owner-badge-text">${safeLabel}</span>
    </span>`;
    if (!isDir) {
      ownerInfo._cachedBadgeCl = num;
      ownerInfo._cachedBadgeRawTarget = rawTarget;
      ownerInfo._cachedBadgeStatus = ownerInfo.status;
      ownerInfo._cachedBadgeHtml = html;
    }
    return html;
  }

  function openPgCodeOwnersDialog(clNum, targetPath, isDir = false) {
    hidePgOwnerTooltip();
    const num = Number(clNum || (state && state.selectedClNumber) || 0);
    if (!num) return;
    if (!state) state = {};
    state.codeOwnersDialogOpen = true;
    state.codeOwnersTarget = { clNum: num, targetPath, isDir };

    renderPgCodeOwnersModalContent(num, targetPath, isDir);

    const backdrop = document.getElementById("pgCodeOwnersBackdrop");
    if (backdrop) {
      backdrop.classList.add("open", "is-open");
      backdrop.setAttribute("aria-hidden", "false");
      const closeBtn = backdrop.querySelector(".pg-dialog-close-btn") || backdrop.querySelector("button");
      if (closeBtn && typeof closeBtn.focus === "function") closeBtn.focus();
    }
  }

  function closePgCodeOwnersDialog() {
    hidePgOwnerTooltip();
    if (state) {
      state.codeOwnersDialogOpen = false;
      state.codeOwnersTarget = null;
    }
    const backdrop = document.getElementById("pgCodeOwnersBackdrop");
    if (backdrop) {
      backdrop.classList.remove("open", "is-open");
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function onPgCodeOwnersBackdropClick(event) {
    if (event.target && (event.target.id === "pgCodeOwnersBackdrop" || event.target === event.currentTarget)) {
      closePgCodeOwnersDialog();
    }
  }

  function onPgFileOwnerBadgeClick(event, clNum, filePath) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    hidePgOwnerTooltip();
    openPgCodeOwnersDialog(clNum, filePath, false);
  }

  function onPgFileOwnerBadgeKeyDown(event, clNum, filePath) {
    if (!event) return;
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
      hidePgOwnerTooltip();
      openPgCodeOwnersDialog(clNum, filePath, false);
    }
  }

  function onPgDirOwnerBadgeClick(event, clNum, dirPath) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    hidePgOwnerTooltip();
    openPgCodeOwnersDialog(clNum, dirPath, true);
  }

  function onPgDirOwnerBadgeKeyDown(event, clNum, dirPath) {
    if (!event) return;
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
      hidePgOwnerTooltip();
      openPgCodeOwnersDialog(clNum, dirPath, true);
    }
  }

  function copyPgOwnersText(text, toastMsg) {
    const str = String(text || "").trim();
    if (!str) return;
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(str).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(toastMsg || "Copied to clipboard!");
    }
  }

  function copyPgOwnersEmails(emailsStr) {
    copyPgOwnersText(emailsStr, "Code owner emails copied to clipboard!");
  }

  function togglePgMissingOwnersFilterFromDialog(clNum) {
    const num = Number(clNum || (state && state.selectedClNumber) || 0);
    closePgCodeOwnersDialog();
    if (num && typeof togglePgOwnersFilter === "function") {
      togglePgOwnersFilter(num);
    }
  }

  async function addPgOwnerAsReviewer(event, clNum, email, name, btnEl) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = `<span class="btn-spinner" style="width:10px;height:10px;border-width:1.5px;"></span> Adding...`;
    }
    try {
      if (typeof submitReviewerAdd === "function") {
        await submitReviewerAdd(clNum, "REVIEWER", email, btnEl);
      }
      if (state && state.codeOwnersDialogOpen && state.codeOwnersTarget) {
        renderPgCodeOwnersModalContent(state.codeOwnersTarget.clNum, state.codeOwnersTarget.targetPath, state.codeOwnersTarget.isDir);
      }
    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = "+ Add Reviewer";
      }
      if (typeof showToast === "function") {
        showToast("Could not add reviewer: " + err.message);
      }
    }
  }

  function renderPgCodeOwnersModalContent(clNum, targetPath, isDir = false) {
    const num = Number(clNum || (state && state.selectedClNumber) || 0);
    const c = ((state && state.changes) || []).find(ch => ch.number === num) || ((state && state.currentChange && state.currentChange.number === num) ? state.currentChange : null);
    const bundle = (state && state.diffsByCl && state.diffsByCl[num]) || null;
    const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];

    const titleEl = document.getElementById("pgCodeOwnersTitle");
    const subtitleEl = document.getElementById("pgCodeOwnersSubtitle");
    const headerIconEl = document.getElementById("pgCodeOwnersHeaderIcon");
    const bodyEl = document.getElementById("pgCodeOwnersBody");
    const footerEl = document.getElementById("pgCodeOwnersFooter");

    if (!bodyEl) return;

    if (isDir) {
      // Directory evaluation
      const rawDir = String(targetPath || "").trim();
      const normDir = rawDir.replace(/\/+$/, "");
      const dirFiles = rawFiles.filter(f => {
        const fileDir = getPgFileDir(f.file).replace(/\/+$/, "");
        return normDir ? fileDir === normDir : !fileDir;
      });
      const fileOwnerStatuses = dirFiles.map(f => ({
        file: f.file,
        statusInfo: getPgFileOwnerStatus(c, f)
      }));
      const missingCount = fileOwnerStatuses.filter(s => s.statusInfo.status === "missing").length;
      const approvedCount = fileOwnerStatuses.filter(s => s.statusInfo.status === "approved").length;
      const isApproved = missingCount === 0;
      const status = isApproved ? "approved" : "missing";

      if (titleEl) titleEl.textContent = `Directory Code Owners: ${normDir || "/"}`;
      if (subtitleEl) subtitleEl.textContent = `ag/${num} · ${dirFiles.length} file${dirFiles.length === 1 ? "" : "s"} in directory`;
      if (headerIconEl) {
        headerIconEl.textContent = isApproved ? "✓" : "✕";
        headerIconEl.className = `pg-action-icon ${isApproved ? "pg-action-icon-success" : "pg-action-icon-warning"}`;
      }

      const ownerFile = normDir ? `//${normDir}/OWNERS` : "//OWNERS";
      const ownerRule = "* (directory default)";

      // Aggregate owners across directory files
      const ownersMap = new Map();
      fileOwnerStatuses.forEach(fo => {
        (fo.statusInfo.owners || []).forEach(o => {
          if (!o || !o.email) return;
          const em = o.email.toLowerCase();
          if (!ownersMap.has(em)) {
            ownersMap.set(em, { ...o });
          } else if (o.approved) {
            ownersMap.get(em).approved = true;
          }
        });
      });
      const ownersList = Array.from(ownersMap.values());
      const ownerEmails = ownersList.map(o => o.email);

      const bannerDesc = isApproved
        ? "All files in this directory are approved by eligible code owners."
        : `${missingCount} of ${dirFiles.length} file(s) in this directory require code owner approval (+2).`;

      let filesHtml = "";
      fileOwnerStatuses.forEach(fo => {
        const isFileApproved = fo.statusInfo.status === "approved";
        const fileIcon = isFileApproved ? "✓" : (fo.statusInfo.status === "na" ? "—" : "✕");
        const fileCls = isFileApproved ? "status-approved" : (fo.statusInfo.status === "na" ? "status-na" : "status-missing");
        const baseName = fo.file.split("/").pop();
        filesHtml += `<div class="pg-owners-dir-file-row">
          <div class="pg-owners-dir-file-left">
            <span class="pg-owner-badge ${fileCls}"><span class="pg-owner-badge-icon">${fileIcon}</span></span>
            <a href="javascript:void(0)" class="pg-owners-file-link" onclick="closePgCodeOwnersDialog(); if (typeof scrollToFileDiff === 'function') scrollToFileDiff(${num}, null, '${escapeHtml(fo.file)}')" title="View diff for ${escapeHtml(fo.file)}">${escapeHtml(baseName)}</a>
          </div>
          <span class="pg-owners-dir-file-meta">${escapeHtml(fo.statusInfo.label || "")}</span>
        </div>`;
      });

      let ownersHtml = "";
      if (ownersList.length === 0) {
        ownersHtml = `<div class="pg-owners-empty">No explicit code owners listed for this directory. Reviewers with +2 can approve.</div>`;
      } else {
        ownersList.forEach(owner => {
          const initials = (owner.name || owner.email || "CO").split(" ").map(p => p[0]).slice(0, 2).join("").toUpperCase();
          ownersHtml += `<div class="pg-owner-item">
            <div class="pg-owner-item-left">
              <div class="pg-owner-avatar">${escapeHtml(initials)}</div>
              <div class="pg-owner-details">
                <span class="pg-owner-name">${escapeHtml(owner.name)}</span>
                <span class="pg-owner-email">${escapeHtml(owner.email)}</span>
              </div>
            </div>
            <div class="pg-owner-item-right">
              <span class="pg-owner-status-chip ${owner.approved ? "is-approved" : (owner.is_reviewer ? "is-reviewer" : "is-eligible")}">
                ${owner.approved ? "✓ Approved (+2)" : (owner.is_reviewer ? "Reviewer" : "Eligible Owner")}
              </span>
              ${!owner.is_reviewer ? `<button type="button" class="btn pg-btn-add-owner" onclick="addPgOwnerAsReviewer(event, ${num}, '${escapeHtml(owner.email)}', '${escapeHtml(owner.name)}', this)" title="Add ${escapeHtml(owner.name)} as reviewer">+ Add Reviewer</button>` : ""}
            </div>
          </div>`;
        });
      }

      bodyEl.innerHTML = `
        <div class="pg-owners-status-banner status-${status}">
          <span class="pg-owners-banner-icon">${status === "approved" ? "✓" : "✕"}</span>
          <div class="pg-owners-banner-info">
            <div class="pg-owners-banner-title">${status === "approved" ? "Directory Code Owners Satisfied" : "Directory Missing Code Owner Approval"}</div>
            <div class="pg-owners-banner-desc">${escapeHtml(bannerDesc)}</div>
          </div>
        </div>
        <div class="pg-owners-section">
          <div class="pg-owners-section-title">Governing OWNERS File & Rule</div>
          <div class="pg-owners-rule-box">
            <div class="pg-owners-rule-meta">
              <span class="pg-owners-rule-file">${escapeHtml(ownerFile)}</span>
              <span class="pg-owners-rule-text">Matched rule: <code>${escapeHtml(ownerRule)}</code></span>
            </div>
            <button type="button" class="pg-btn-subtle pg-btn-copy-rule" onclick="copyPgOwnersText('${escapeHtml(ownerFile)}', 'OWNERS path copied!')" title="Copy OWNERS path">📋 Copy</button>
          </div>
        </div>
        <div class="pg-owners-section">
          <div class="pg-owners-section-title">Eligible Code Owners & Approvers (${ownersList.length})</div>
          <div class="pg-owners-list">
            ${ownersHtml}
          </div>
        </div>
        <div class="pg-owners-section">
          <div class="pg-owners-section-title">Directory Files (${dirFiles.length})</div>
          <div class="pg-owners-dir-files">
            ${filesHtml || `<div class="pg-owners-empty">No files in directory.</div>`}
          </div>
        </div>
      `;

      if (footerEl) {
        footerEl.innerHTML = `
          <div style="display:flex;align-items:center;gap:8px;">
            ${ownerEmails.length ? `<button type="button" class="pg-btn-flat" onclick="copyPgOwnersEmails('${escapeHtml(ownerEmails.join(", "))}')" title="Copy owner emails to clipboard">📋 Copy Emails</button>` : ""}
            <button type="button" class="pg-btn-flat" onclick="togglePgMissingOwnersFilterFromDialog(${num})" title="Filter file list to show only files missing owner approval">🔍 Filter Missing Files</button>
          </div>
          <div style="flex:1;"></div>
          <button type="button" class="pg-btn-primary" onclick="closePgCodeOwnersDialog()">Close (Esc)</button>
        `;
      }
      return;
    }

    // Single File evaluation
    const safeFilePath = String(targetPath || "");
    const f = rawFiles.find(file => file.file === safeFilePath) || { file: safeFilePath };
    const ownerInfo = getPgFileOwnerStatus(c, f);
    const status = ownerInfo.status;
    const isApproved = status === "approved";

    if (titleEl) titleEl.textContent = `Code Owners: ${safeFilePath.split("/").pop() || safeFilePath}`;
    if (subtitleEl) subtitleEl.textContent = `ag/${num} · ${safeFilePath}`;
    if (headerIconEl) {
      if (status === "approved") {
        headerIconEl.textContent = "✓";
        headerIconEl.className = "pg-action-icon pg-action-icon-success";
      } else if (status === "missing") {
        headerIconEl.textContent = "✕";
        headerIconEl.className = "pg-action-icon pg-action-icon-warning";
      } else {
        headerIconEl.textContent = "—";
        headerIconEl.className = "pg-action-icon";
      }
    }

    const bannerDesc = isApproved
      ? (ownerInfo.title || "This file is approved by an eligible code owner.")
      : (status === "missing" ? "This file requires Code-Review +2 from an eligible code owner." : "Code ownership does not apply to this file.");

    const ownersList = Array.isArray(ownerInfo.owners) ? ownerInfo.owners : [];
    const ownerEmails = ownersList.map(o => o.email).filter(Boolean);

    let ownersHtml = "";
    if (status === "na") {
      ownersHtml = `<div class="pg-owners-empty">Code owner approvals are not required for magic files like /COMMIT_MSG and /MERGE_LIST.</div>`;
    } else if (ownersList.length === 0) {
      ownersHtml = `<div class="pg-owners-empty">No explicit code owners defined in OWNERS file. Project owners or reviewers with +2 can approve.</div>`;
    } else {
      ownersList.forEach(owner => {
        const initials = (owner.name || owner.email || "CO").split(" ").map(p => p[0]).slice(0, 2).join("").toUpperCase();
        ownersHtml += `<div class="pg-owner-item">
          <div class="pg-owner-item-left">
            <div class="pg-owner-avatar">${escapeHtml(initials)}</div>
            <div class="pg-owner-details">
              <span class="pg-owner-name">${escapeHtml(owner.name)}</span>
              <span class="pg-owner-email">${escapeHtml(owner.email)}</span>
            </div>
          </div>
          <div class="pg-owner-item-right">
            <span class="pg-owner-status-chip ${owner.approved ? "is-approved" : (owner.is_reviewer ? "is-reviewer" : "is-eligible")}">
              ${owner.approved ? "✓ Approved (+2)" : (owner.is_reviewer ? "Reviewer" : "Eligible Owner")}
            </span>
            ${!owner.is_reviewer ? `<button type="button" class="btn pg-btn-add-owner" onclick="addPgOwnerAsReviewer(event, ${num}, '${escapeHtml(owner.email)}', '${escapeHtml(owner.name)}', this)" title="Add ${escapeHtml(owner.name)} as reviewer">+ Add Reviewer</button>` : ""}
          </div>
        </div>`;
      });
    }

    bodyEl.innerHTML = `
      <div class="pg-owners-status-banner status-${status}">
        <span class="pg-owners-banner-icon">${status === "approved" ? "✓" : (status === "missing" ? "✕" : "—")}</span>
        <div class="pg-owners-banner-info">
          <div class="pg-owners-banner-title">${status === "approved" ? "Code Owner Approved" : (status === "missing" ? "Missing Code Owner Approval (+2)" : "Code Owners Not Applicable")}</div>
          <div class="pg-owners-banner-desc">${escapeHtml(bannerDesc)}</div>
        </div>
      </div>
      ${status !== "na" ? `
      <div class="pg-owners-section">
        <div class="pg-owners-section-title">Governing OWNERS File & Rule</div>
        <div class="pg-owners-rule-box">
          <div class="pg-owners-rule-meta">
            <span class="pg-owners-rule-file">${escapeHtml(ownerInfo.owner_file || "//OWNERS")}</span>
            <span class="pg-owners-rule-text">Matched rule: <code>${escapeHtml(ownerInfo.owner_rule || "* (directory default)")}</code></span>
          </div>
          <button type="button" class="pg-btn-subtle pg-btn-copy-rule" onclick="copyPgOwnersText('${escapeHtml(ownerInfo.owner_file || "//OWNERS")}', 'OWNERS path copied!')" title="Copy OWNERS path">📋 Copy</button>
        </div>
      </div>
      <div class="pg-owners-section">
        <div class="pg-owners-section-title">Eligible Code Owners & Approvers (${ownersList.length})</div>
        <div class="pg-owners-list">
          ${ownersHtml}
        </div>
      </div>
      ` : `
      <div class="pg-owners-section">
        <div class="pg-owners-list">
          ${ownersHtml}
        </div>
      </div>
      `}
    `;

    if (footerEl) {
      footerEl.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;">
          ${ownerEmails.length ? `<button type="button" class="pg-btn-flat" onclick="copyPgOwnersEmails('${escapeHtml(ownerEmails.join(", "))}')" title="Copy owner emails to clipboard">📋 Copy Emails</button>` : ""}
          <button type="button" class="pg-btn-flat" onclick="togglePgMissingOwnersFilterFromDialog(${num})" title="Filter file list to show only files missing owner approval">🔍 Filter Missing Files</button>
        </div>
        <div style="flex:1;"></div>
        <button type="button" class="pg-btn-primary" onclick="closePgCodeOwnersDialog()">Close (Esc)</button>
      `;
    }
  }

  function getPgFileQueryFilter(clNum) {
    if (!state.pgFileQueryFilterByCl) state.pgFileQueryFilterByCl = {};
    return String(state.pgFileQueryFilterByCl[clNum] || "").trim();
  }

  function reanchorPgFocusedFile(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const visible = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : [];
    const cur = state.focusedFileByCl && state.focusedFileByCl[num];
    if (cur && visible.length > 0 && !visible.includes(cur)) {
      focusPgFileRow(num, visible[0], { scrollIntoView: false, focusElement: false });
    }
  }

  function setPgFileQueryFilter(clNum, query) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgFileQueryFilterByCl) state.pgFileQueryFilterByCl = {};
    state.pgFileQueryFilterByCl[num] = String(query || "");
    reanchorPgFocusedFile(num);
    applyPgFileFiltersToDom(num);
  }

  function isPgFuzzySubsequence(pattern, str) {
    if (!pattern) return true;
    if (!str) return false;
    const p = String(pattern).toLowerCase();
    const s = String(str).toLowerCase();
    let pIdx = 0;
    let sIdx = 0;
    while (pIdx < p.length && sIdx < s.length) {
      if (p[pIdx] === s[sIdx]) {
        pIdx++;
      }
      sIdx++;
    }
    return pIdx === p.length;
  }

  function highlightPgFilePathMatch(filePath, query) {
    const rawPath = String(filePath || "");
    const q = String(query || "").trim();
    if (!q) return escapeHtml(rawPath);

    const tokens = q.split(/\s+/).filter(Boolean);
    const searchTerms = [];
    tokens.forEach(tok => {
      if (tok.startsWith("-")) return;
      let cleaned = tok;
      if (cleaned.startsWith("ext:")) cleaned = cleaned.substring(4);
      else if (cleaned.startsWith("path:") || cleaned.startsWith("dir:")) cleaned = cleaned.replace(/^(path|dir):/, "");
      else if (/^(is|status|has|missing):/i.test(cleaned)) return;
      cleaned = cleaned.replace(/[*?]/g, "").trim();
      if (cleaned) searchTerms.push(cleaned.toLowerCase());
    });

    if (!searchTerms.length) return escapeHtml(rawPath);

    const lowerPath = rawPath.toLowerCase();
    const marked = new Array(rawPath.length).fill(false);

    searchTerms.forEach(term => {
      let startIdx = 0;
      let foundAny = false;
      while ((startIdx = lowerPath.indexOf(term, startIdx)) !== -1) {
        foundAny = true;
        for (let i = startIdx; i < startIdx + term.length; i++) {
          marked[i] = true;
        }
        startIdx += term.length;
      }
      if (!foundAny && term.length >= 2 && isPgFuzzySubsequence(term, lowerPath)) {
        let tIdx = 0;
        for (let pIdx = 0; pIdx < lowerPath.length && tIdx < term.length; pIdx++) {
          if (lowerPath[pIdx] === term[tIdx]) {
            marked[pIdx] = true;
            tIdx++;
          }
        }
      }
    });

    let html = "";
    let inMark = false;
    let currentSlice = "";

    for (let i = 0; i < rawPath.length; i++) {
      const isMarked = marked[i];
      if (isMarked !== inMark) {
        if (currentSlice) {
          html += inMark ? `<mark class="pg-file-match-highlight">${escapeHtml(currentSlice)}</mark>` : escapeHtml(currentSlice);
          currentSlice = "";
        }
        inMark = isMarked;
      }
      currentSlice += rawPath[i];
    }
    if (currentSlice) {
      html += inMark ? `<mark class="pg-file-match-highlight">${escapeHtml(currentSlice)}</mark>` : escapeHtml(currentSlice);
    }
    return html;
  }

  function getPgHideReviewedPreference() {
    if (typeof window !== "undefined" && window.localStorage) {
      try {
        return window.localStorage.getItem("gerrit_ui_pg_hide_reviewed") === "true";
      } catch (_) {}
    }
    return false;
  }

  function setPgHideReviewedPreference(val) {
    if (typeof window !== "undefined" && window.localStorage) {
      try {
        window.localStorage.setItem("gerrit_ui_pg_hide_reviewed", val ? "true" : "false");
      } catch (_) {}
    }
  }

  function isPgHideReviewedActive(clNum) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (state.hideReviewedByCl && (num in state.hideReviewedByCl)) {
      return Boolean(state.hideReviewedByCl[num]);
    }
    return getPgHideReviewedPreference();
  }

  function setPgHideReviewed(clNum, val) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    state.hideReviewedByCl = state.hideReviewedByCl || {};
    state.hideReviewedByCl[num] = Boolean(val);
    setPgHideReviewedPreference(Boolean(val));
    applyPgFileFiltersToDom(num);
  }

  function togglePgHideReviewed(clNum, forceVal) {
    const num = Number(clNum || state.selectedClNumber || (state.changes && state.changes[0] && state.changes[0].number) || 0);
    if (!num) return;
    const current = isPgHideReviewedActive(num);
    const next = (forceVal !== undefined) ? Boolean(forceVal) : !current;
    setPgHideReviewed(num, next);
    if (next) {
      const visibleFiles = (typeof getPgVisibleFileList === "function") ? getPgVisibleFileList(num) : [];
      if (visibleFiles.length > 0) {
        const curFocused = (state.focusedFileByCl && state.focusedFileByCl[num]) || "";
        if (!visibleFiles.includes(curFocused) && typeof focusPgFileRow === "function") {
          focusPgFileRow(num, visibleFiles[0], { scrollIntoView: false });
        }
      }
    }
    if (typeof showToast === "function") {
      showToast(next ? "Filtering file list: showing unreviewed files only (h) ✓" : "Showing all files in change (including reviewed)");
    }
  }

  function matchesPgFileFilter(filePath, query, meta = {}) {
    const rawQuery = String(query || "").trim();
    if (!rawQuery) return true;
    const path = String(filePath || "");
    const lowerPath = path.toLowerCase();
    const oldPath = String((meta && (meta.oldPath || meta.old_path)) || "");
    const lowerOldPath = oldPath.toLowerCase();

    if (lowerPath === "/commit_msg") {
      const qLower = rawQuery.toLowerCase();
      if (qLower === "commit" || qLower === "msg" || qLower === "message" || qLower.includes("/commit_msg")) {
        return true;
      }
    }

    if (lowerPath === "/merge_list") {
      const qLower = rawQuery.toLowerCase();
      if (qLower === "merge" || qLower === "list" || qLower === "mergelist" || qLower.includes("/merge_list")) {
        return true;
      }
    }

    const tokens = rawQuery.split(/\s+/).filter(Boolean);
    if (!tokens.length) return true;

    for (const rawToken of tokens) {
      let isNegated = false;
      let token = rawToken;
      if (token.startsWith("-") && token.length > 1) {
        isNegated = true;
        token = token.substring(1);
      }
      const lowerToken = token.toLowerCase();
      let tokenMatches = false;

      if (lowerToken.startsWith("ext:")) {
        const ext = lowerToken.substring(4).replace(/^\./, "");
        tokenMatches = lowerPath.endsWith("." + ext) || (lowerOldPath ? lowerOldPath.endsWith("." + ext) : false);
      } else if (lowerToken.startsWith(".") && lowerToken.length > 1 && !lowerToken.includes("/") && !lowerToken.includes("*")) {
        tokenMatches = lowerPath.endsWith(lowerToken) || (lowerOldPath ? lowerOldPath.endsWith(lowerToken) : false);
      } else if (lowerToken.startsWith("dir:") || lowerToken.startsWith("path:")) {
        const prefix = lowerToken.startsWith("dir:") ? lowerToken.substring(4) : lowerToken.substring(5);
        tokenMatches = lowerPath.includes(prefix) || (lowerOldPath ? lowerOldPath.includes(prefix) : false);
      } else if (lowerToken.startsWith("status:")) {
        const expectedStatus = lowerToken.substring(7).toUpperCase();
        tokenMatches = meta.status ? (meta.status === expectedStatus) : false;
      } else if (lowerToken.startsWith("size:")) {
        let expectedSize = lowerToken.substring(5).toUpperCase();
        if (expectedSize === "EXTRA-SMALL" || expectedSize === "EXTRASMALL") expectedSize = "XS";
        else if (expectedSize === "SMALL") expectedSize = "S";
        else if (expectedSize === "MEDIUM") expectedSize = "M";
        else if (expectedSize === "LARGE") expectedSize = "L";
        else if (expectedSize === "EXTRA-LARGE" || expectedSize === "EXTRALARGE") expectedSize = "XL";
        tokenMatches = meta.sizeCategory ? (meta.sizeCategory === expectedSize) : false;
      } else if (lowerToken === "is:xs" || lowerToken === "is:s" || lowerToken === "is:m" || lowerToken === "is:l" || lowerToken === "is:xl") {
        const expectedSize = lowerToken.substring(3).toUpperCase();
        tokenMatches = meta.sizeCategory ? (meta.sizeCategory === expectedSize) : false;
      } else if (lowerToken === "is:modified") {
        tokenMatches = meta.status === "M";
      } else if (lowerToken === "is:added") {
        tokenMatches = meta.status === "A";
      } else if (lowerToken === "is:deleted") {
        tokenMatches = meta.status === "D";
      } else if (lowerToken === "is:renamed") {
        tokenMatches = meta.status === "R";
      } else if (lowerToken === "is:reviewed") {
        tokenMatches = Boolean(meta.isReviewed);
      } else if (lowerToken === "is:unreviewed") {
        tokenMatches = !meta.isReviewed;
      } else if (lowerToken === "has:comments" || lowerToken === "is:commented") {
        tokenMatches = Boolean(meta.hasComments);
      } else if (lowerToken === "has:drafts" || lowerToken === "has:draft" || lowerToken === "is:draft") {
        tokenMatches = Boolean(meta.hasDrafts);
      } else if (lowerToken === "has:unresolved" || lowerToken === "is:unresolved") {
        tokenMatches = Boolean(meta.hasUnresolved);
      } else if (lowerToken === "has:resolved" || lowerToken === "is:resolved") {
        tokenMatches = Boolean(meta.hasResolved);
      } else if (lowerToken === "has:robot" || lowerToken === "is:robot") {
        tokenMatches = Boolean(meta.hasRobot);
      } else if (lowerToken === "missing:owner" || lowerToken === "owner:missing" || lowerToken === "is:missing-owner") {
        tokenMatches = Boolean(meta.isOwnerMissing);
      } else if (lowerToken === "checks:failed" || lowerToken === "checks:error" || lowerToken === "checks:fail" || lowerToken === "has:failed-checks" || lowerToken === "is:failed-checks") {
        tokenMatches = Boolean(meta.hasFailedChecks || (meta.checksStatus === "failed"));
      } else if (lowerToken === "checks:warning" || lowerToken === "checks:warn" || lowerToken === "has:warning-checks") {
        tokenMatches = Boolean(meta.hasWarningChecks || (meta.checksStatus === "warning"));
      } else if (lowerToken === "checks:running" || lowerToken === "checks:queued" || lowerToken === "has:running-checks") {
        tokenMatches = Boolean(meta.hasRunningChecks || (meta.checksStatus === "running"));
      } else if (lowerToken === "checks:passed" || lowerToken === "checks:pass" || lowerToken === "checks:ok" || lowerToken === "checks:success") {
        tokenMatches = Boolean(meta.hasPassedChecks || (meta.checksStatus === "passed"));
      } else if (lowerToken === "has:checks" || lowerToken === "is:checked") {
        tokenMatches = Boolean(meta.hasChecks);
      } else if (token.includes("*") || token.includes("?")) {
        const regexPattern = "^" + token.split("*").map(part => part.split("?").map(sub => sub.replace(/[-\/\\^$+?.()|[\]{}]/g, '\\$&')).join(".")).join(".*") + "$";
        try {
          tokenMatches = new RegExp(regexPattern, "i").test(path) || (lowerOldPath ? new RegExp(regexPattern, "i").test(oldPath) : false);
        } catch (_) {
          tokenMatches = lowerPath.includes(lowerToken) || (lowerOldPath && lowerOldPath.includes(lowerToken));
        }
      } else if (lowerPath.includes(lowerToken) || (lowerOldPath && lowerOldPath.includes(lowerToken))) {
        tokenMatches = true;
      } else if (lowerToken.length >= 2 && (isPgFuzzySubsequence(lowerToken, lowerPath) || (lowerOldPath && isPgFuzzySubsequence(lowerToken, lowerOldPath)))) {
        tokenMatches = true;
      }

      if (isNegated) {
        if (tokenMatches) return false;
      } else {
        if (!tokenMatches) return false;
      }
    }

    return true;
  }

  function isPgFileFilterActive(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return false;
    const query = String((state.pgFileQueryFilterByCl && state.pgFileQueryFilterByCl[num]) || "").trim();
    const isOwner = Boolean(state.pgOwnerFilterByCl && state.pgOwnerFilterByCl[num]);
    const isComments = Boolean(state.pgCommentsFilterByCl && state.pgCommentsFilterByCl[num]);
    const status = String((state.fileStatusFilterByCl && state.fileStatusFilterByCl[num]) || "").trim();
    const ext = String((state.fileExtensionFilterByCl && state.fileExtensionFilterByCl[num]) || "").trim();
    const size = String((state.fileSizeFilterByCl && state.fileSizeFilterByCl[num]) || "").trim();
    const hideRev = Boolean(state.hideReviewedByCl && state.hideReviewedByCl[num]);
    return Boolean(query || isOwner || isComments || status || ext || size || hideRev);
  }

  function isPgOwnerFilterActive(clNum) {
    if (!state.pgOwnerFilterByCl) state.pgOwnerFilterByCl = {};
    return Boolean(state.pgOwnerFilterByCl[clNum]);
  }

  function togglePgOwnersFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgOwnerFilterByCl) state.pgOwnerFilterByCl = {};
    state.pgOwnerFilterByCl[num] = !state.pgOwnerFilterByCl[num];
    const isActive = state.pgOwnerFilterByCl[num];
    if (typeof showToast === "function") {
      showToast(isActive ? "Filtering file list: showing files missing code owner approval" : "Showing all files in change");
    }
    applyPgFileFiltersToDom(num);
  }

  function isPgCommentsFilterActive(clNum) {
    if (!state.pgCommentsFilterByCl) state.pgCommentsFilterByCl = {};
    return Boolean(state.pgCommentsFilterByCl[clNum]);
  }

  function getPgCommentsFilterMode(clNum) {
    if (!state.pgCommentsFilterByCl) state.pgCommentsFilterByCl = {};
    const val = state.pgCommentsFilterByCl[clNum];
    if (typeof val === "string") return val;
    return val ? "unresolved" : "";
  }

  function setPgCommentsFilterMode(clNum, mode) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgCommentsFilterByCl) state.pgCommentsFilterByCl = {};
    state.pgCommentsFilterByCl[num] = mode || false;
    applyPgFileFiltersToDom(num);
  }

  function togglePgCommentsFilter(clNum, targetMode = null) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgCommentsFilterByCl) state.pgCommentsFilterByCl = {};
    const curMode = getPgCommentsFilterMode(num);
    const mode = targetMode || (curMode ? "" : "unresolved");

    if (curMode === mode || (!mode && curMode)) {
      state.pgCommentsFilterByCl[num] = false;
      if (typeof showToast === "function") {
        showToast("Showing all files in change");
      }
    } else {
      state.pgCommentsFilterByCl[num] = mode;
      if (typeof showToast === "function") {
        if (mode === "comments") {
          showToast("Filtering file list: showing files with comments or drafts (Shift+H) 💬");
        } else if (mode === "drafts") {
          showToast("Filtering file list: showing files with unpublished drafts 📝");
        } else {
          showToast("Filtering file list: showing files with unresolved comments or drafts (Shift+H) ⚠️");
        }
      }
    }
    applyPgFileFiltersToDom(num);
  }

  function togglePgCommentsFilterMode(clNum, mode) {
    togglePgCommentsFilter(clNum, mode);
  }

  function cyclePgCommentsFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const curMode = getPgCommentsFilterMode(num);
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    let hasAnyComments = false;
    let hasAnyUnres = false;
    let hasAnyDrafts = false;
    rawFiles.forEach(f => {
      const st = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, f.file) : {};
      if (st.hasComments || st.total > 0 || st.commentCount > 0) hasAnyComments = true;
      if (st.hasUnresolved) hasAnyUnres = true;
      if (st.hasDrafts) hasAnyDrafts = true;
    });

    if (!curMode) {
      if (hasAnyUnres) togglePgCommentsFilter(num, "unresolved");
      else if (hasAnyComments) togglePgCommentsFilter(num, "comments");
      else if (hasAnyDrafts) togglePgCommentsFilter(num, "drafts");
      else if (typeof showToast === "function") showToast("No comments or drafts in this change");
    } else if (curMode === "unresolved") {
      if (hasAnyComments) togglePgCommentsFilter(num, "comments");
      else togglePgCommentsFilter(num, "");
    } else {
      togglePgCommentsFilter(num, "");
    }
  }

  function clearPgCommentsFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgCommentsFilterByCl) state.pgCommentsFilterByCl = {};
    state.pgCommentsFilterByCl[num] = false;
    applyPgFileFiltersToDom(num);
    if (typeof showToast === "function") {
      showToast("Comments filter cleared ✓");
    }
  }

  function clearPgFileFilter(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgFileQueryFilterByCl) state.pgFileQueryFilterByCl = {};
    state.pgFileQueryFilterByCl[num] = "";
    const input = document.getElementById(`pgFileFilterInput-${num}`);
    if (input) {
      input.value = "";
    }
    applyPgFileFiltersToDom(num);
    if (typeof showToast === "function") {
      showToast("File filter cleared ✓");
    }
  }

  function clearAllPgFileFilters(clNum, showNotification = true) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgFileQueryFilterByCl) state.pgFileQueryFilterByCl = {};
    state.pgFileQueryFilterByCl[num] = "";
    if (!state.pgOwnerFilterByCl) state.pgOwnerFilterByCl = {};
    state.pgOwnerFilterByCl[num] = false;
    if (!state.pgCommentsFilterByCl) state.pgCommentsFilterByCl = {};
    state.pgCommentsFilterByCl[num] = false;
    if (!state.fileStatusFilterByCl) state.fileStatusFilterByCl = {};
    state.fileStatusFilterByCl[num] = "";
    if (!state.fileExtensionFilterByCl) state.fileExtensionFilterByCl = {};
    state.fileExtensionFilterByCl[num] = "";
    if (!state.fileSizeFilterByCl) state.fileSizeFilterByCl = {};
    state.fileSizeFilterByCl[num] = "";
    if (!state.hideReviewedByCl) state.hideReviewedByCl = {};
    state.hideReviewedByCl[num] = false;
    setPgHideReviewedPreference(false);
    const input = document.getElementById(`pgFileFilterInput-${num}`);
    if (input) {
      input.value = "";
    }
    const hideCb = document.getElementById(`pgHideReviewedCb-${num}`);
    if (hideCb) {
      hideCb.checked = false;
    }
    const clearBtn = document.getElementById(`pgFileFilterClearBtn-${num}`);
    if (clearBtn) {
      clearBtn.style.display = "none";
    }
    applyPgFileFiltersToDom(num);
    if (showNotification && typeof showToast === "function") {
      const bundle = state.diffsByCl && state.diffsByCl[num];
      const count = (bundle && Array.isArray(bundle.files)) ? bundle.files.length : "all";
      showToast(`All file filters cleared ✓ (Showing all ${count} files)`);
    }
  }

  function onPgFileFilterInput(clNum, val) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgFileQueryFilterByCl) state.pgFileQueryFilterByCl = {};
    state.pgFileQueryFilterByCl[num] = String(val || "");
    applyPgFileFiltersToDom(num);
  }

  function onPgFileFilterKeyDown(event, clNum) {
    if (!event) return;
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (event.key === "Escape") {
      event.preventDefault();
      const input = document.getElementById(`pgFileFilterInput-${num}`);
      if (input && input.value) {
        clearPgFileFilter(num);
      }
      const visibleFiles = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : [];
      const curFocused = (state.focusedFileByCl && state.focusedFileByCl[num]) || (visibleFiles.length > 0 ? visibleFiles[0] : null);
      if (curFocused) {
        focusPgFileRow(num, curFocused, { focusElement: true, scrollIntoView: true });
      } else if (input) {
        input.blur();
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      const visibleFiles = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : [];
      const firstFile = visibleFiles[0];
      if (firstFile) {
        focusPgFileRow(num, firstFile, { focusElement: true, scrollIntoView: true });
      }
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const visibleFiles = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : [];
      const firstFile = visibleFiles[0];
      if (firstFile) {
        focusPgFileRow(num, firstFile, { focusElement: true, scrollIntoView: true });
        const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
        const c = (state.changes || []).find(ch => ch.number === num);
        const ps = Number(selPsMeta.ps || (c && c.patch_set) || 1);
        onPgFileRowClick(event, num, ps, firstFile);
      }
      return;
    }
    if ((event.key === "s" || event.key === "S") && event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      cyclePgFileStatusFilter(num);
      return;
    }
    if ((event.key === "e" || event.key === "E") && event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      cyclePgFileExtensionFilter(num);
      return;
    }
    if ((event.key === "b" || event.key === "B") && event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      cyclePgFileSizeFilter(num);
      return;
    }
  }

  function applyPgFileFiltersToDom(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const query = getPgFileQueryFilter(num);
    const isOwnerFilter = isPgOwnerFilterActive(num);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;

    const clearBtn = document.getElementById(`pgFileFilterClearBtn-${num}`);
    if (clearBtn) {
      clearBtn.style.display = query ? "" : "none";
    }

    const isHideRev = (typeof isPgHideReviewedActive === "function") && isPgHideReviewedActive(num);
    const hideRevCb = document.getElementById(`pgHideReviewedCb-${num}`);
    if (hideRevCb) {
      hideRevCb.checked = Boolean(isHideRev);
    }

    const rows = Array.from(changeView.querySelectorAll(".pg-file-list-table tbody tr.pg-file-row:not(.pg-file-row-empty)"));
    let visibleCount = 0;
    let totalFiles = 0;
    let visibleReviewedCount = 0;
    let hiddenReviewedCount = 0;
    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const curPs = Number(selPsMeta.ps || (state.diffsByCl[num] && state.diffsByCl[num].patch_set) || 1);
    const revSet = getPgReviewedSet(num, curPs);

    const isCommentsFilter = (typeof isPgCommentsFilterActive === "function") && isPgCommentsFilterActive(num);
    const commentsMode = (typeof getPgCommentsFilterMode === "function") ? getPgCommentsFilterMode(num) : (isCommentsFilter ? "unresolved" : "");
    const activeStatus = (typeof getPgFileStatusFilter === "function") ? getPgFileStatusFilter(num) : "";
    const activeExt = (typeof getPgFileExtensionFilter === "function") ? getPgFileExtensionFilter(num) : "";
    const activeSize = (typeof getPgFileSizeFilter === "function") ? getPgFileSizeFilter(num) : "";

    rows.forEach(r => {
      const isMagic = r.classList.contains("pg-magic-file-row");
      const filePath = r.getAttribute("data-file") || "";
      const rowStatus = r.getAttribute("data-status") || (isMagic ? "M" : "M");
      const ownerStatus = r.getAttribute("data-owner-status") || "";
      const isRev = r.classList.contains("is-reviewed") || revSet.has(filePath);
      let matchesQuery = false;
      let matchesOwner = false;
      let matchesComments = true;
      let matchesHideReviewed = true;
      let matchesStatus = !activeStatus || (rowStatus === activeStatus);
      const rowExt = r.getAttribute("data-extension") || ((typeof getPgFileExtension === "function") ? getPgFileExtension(filePath) : "");
      let matchesExtension = isMagic ? !activeExt : (!activeExt || (rowExt === activeExt));
      const rowSize = r.getAttribute("data-size") || "";
      let matchesSize = isMagic ? !activeSize : (!activeSize || (rowSize === activeSize));

      if (isMagic) {
        matchesOwner = !isOwnerFilter;
        matchesQuery = !query || matchesPgFileFilter("/COMMIT_MSG", query, { status: "M" });
        if (isCommentsFilter) {
          const stats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, "/COMMIT_MSG") : {};
          if (commentsMode === "drafts") {
            matchesComments = Boolean(stats.hasDrafts);
          } else if (commentsMode === "comments") {
            matchesComments = Boolean(stats.hasComments || stats.total > 0 || stats.hasDrafts);
          } else {
            matchesComments = Boolean(stats.hasUnresolved || stats.hasDrafts);
          }
        }
      } else {
        totalFiles++;
        if (isRev && isHideRev) {
          matchesHideReviewed = false;
          hiddenReviewedCount++;
        }
        matchesOwner = !isOwnerFilter || ownerStatus === "missing";
        const stats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, filePath) : {};
        if (isCommentsFilter) {
          if (commentsMode === "drafts") {
            matchesComments = Boolean(stats.hasDrafts);
          } else if (commentsMode === "comments") {
            matchesComments = Boolean(stats.hasComments || stats.total > 0 || stats.hasDrafts);
          } else {
            matchesComments = Boolean(stats.hasUnresolved || stats.hasDrafts);
          }
        }
        matchesQuery = matchesPgFileFilter(filePath, query, {
          status: rowStatus,
          extension: rowExt,
          sizeCategory: rowSize,
          isReviewed: isRev,
          hasComments: Boolean(stats.hasComments || stats.total > 0 || stats.commentCount > 0),
          hasDrafts: Boolean(stats.hasDrafts || stats.draftCount > 0),
          hasUnresolved: Boolean(stats.hasUnresolved || stats.unresolvedCount > 0),
          hasResolved: Boolean(stats.hasResolved || stats.resolvedCount > 0),
          hasRobot: Boolean(stats.hasRobot || stats.robotCount > 0),
          isOwnerMissing: ownerStatus === "missing"
        });

        // Update match highlighting in file row link
        const link = r.querySelector(".pg-file-row-link");
        if (link) {
          const typeIcon = link.querySelector(".pg-file-type-icon");
          const typeIconHtml = typeIcon ? typeIcon.outerHTML : "";
          const treeName = link.querySelector(".pg-tree-file-name");
          if (treeName) {
            const basename = filePath.split("/").pop() || filePath;
            treeName.innerHTML = highlightPgFilePathMatch(basename, query);
          } else {
            link.innerHTML = typeIconHtml + highlightPgFilePathMatch(filePath, query);
          }
        }
      }

      const visible = matchesQuery && matchesOwner && matchesComments && matchesHideReviewed && matchesStatus && matchesExtension && matchesSize;
      r.style.display = visible ? "" : "none";
      r.setAttribute("data-matches-filter", visible ? "true" : "false");
      if (visible && !isMagic) {
        visibleCount++;
        if (isRev) {
          visibleReviewedCount++;
        }
      }
    });

    const diffBlocks = Array.from(changeView.querySelectorAll(`.pg-files-container .pg-file-diff-block[data-cl="${num}"]`));
    diffBlocks.forEach(b => {
      const filePath = b.getAttribute("data-file") || "";
      const isRev = revSet.has(filePath);
      if (isRev && isHideRev) {
        b.style.display = "none";
        return;
      }
      const row = rows.find(r => r.getAttribute("data-file") === filePath);
      const rowStatus = (row ? row.getAttribute("data-status") : "") || b.getAttribute("data-status") || "M";
      const matchesStatus = !activeStatus || (rowStatus === activeStatus);
      const rowSize = row ? (row.getAttribute("data-size") || "") : (b.getAttribute("data-size") || "");
      const matchesSize = !activeSize || (rowSize === activeSize);
      const matchesQuery = matchesPgFileFilter(filePath, query, { status: rowStatus, sizeCategory: rowSize });
      const ownerStatus = row ? row.getAttribute("data-owner-status") : "";
      const matchesOwner = !isOwnerFilter || ownerStatus === "missing";
      let matchesComments = true;
      if (isCommentsFilter) {
        const stats = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(num, filePath) : {};
        if (commentsMode === "drafts") {
          matchesComments = Boolean(stats.hasDrafts);
        } else if (commentsMode === "comments") {
          matchesComments = Boolean(stats.hasComments || stats.total > 0 || stats.hasDrafts);
        } else {
          matchesComments = Boolean(stats.hasUnresolved || stats.hasDrafts);
        }
      }
      b.style.display = (matchesQuery && matchesOwner && matchesComments && matchesStatus && matchesSize) ? "" : "none";
    });

    const hasSearchOrFilter = Boolean(query || isOwnerFilter || isCommentsFilter || activeStatus || activeExt || activeSize || (isHideRev && hiddenReviewedCount > 0));
    const collapsedSet = (typeof getPgCollapsedDirs === "function") ? getPgCollapsedDirs(num) : new Set();
    const dirRows = Array.from(changeView.querySelectorAll(".pg-file-list-table tbody tr.pg-dir-row"));
    dirRows.forEach(dr => {
      const dir = dr.getAttribute("data-dir") || "";
      const isDirPersistedCollapsed = collapsedSet.has(dir);
      const childRows = rows.filter(r => r.getAttribute("data-dir") === dir);
      const matchingChildCount = childRows.filter(r => r.getAttribute("data-matches-filter") === "true").length;
      const anyChildVisible = matchingChildCount > 0;
      dr.style.display = anyChildVisible ? "" : "none";
      if (anyChildVisible) {
        const shouldCollapse = isDirPersistedCollapsed && !hasSearchOrFilter;
        dr.classList.toggle("is-collapsed", shouldCollapse);
        dr.setAttribute("aria-expanded", shouldCollapse ? "false" : "true");
        const chevron = dr.querySelector(".pg-dir-chevron");
        if (chevron) chevron.textContent = shouldCollapse ? "▶" : "▼";
        childRows.forEach(r => {
          if (r.getAttribute("data-matches-filter") === "true") {
            r.style.display = shouldCollapse ? "none" : "";
          } else {
            r.style.display = "none";
          }
        });
      }
    });

    const visibleFiles = (typeof getPgAllVisibleFiles === "function") ? getPgAllVisibleFiles(num) : [];
    const curFocused = state.focusedFileByCl && state.focusedFileByCl[num];
    if (curFocused && visibleFiles.length > 0 && !visibleFiles.includes(curFocused)) {
      focusPgFileRow(num, visibleFiles[0], { scrollIntoView: false, focusElement: false });
    }

    const hasAnyFilter = Boolean(query || isOwnerFilter || isCommentsFilter || (isHideRev && hiddenReviewedCount > 0) || activeStatus || activeExt || activeSize);
    let emptyRow = changeView.querySelector(".pg-file-row-empty");
    if (visibleCount === 0 && hasAnyFilter) {
      if (!emptyRow) {
        const tbody = changeView.querySelector(".pg-file-list-table tbody");
        if (tbody) {
          emptyRow = document.createElement("tr");
          emptyRow.className = "pg-file-row pg-file-row-empty";
          tbody.appendChild(emptyRow);
        }
      }
      if (emptyRow) {
        emptyRow.style.display = "";
        const queryEscaped = escapeHtml(query);
        let emptyMsg = `No files match filter "${queryEscaped}".`;
        if (activeStatus && !query && !isOwnerFilter && !isCommentsFilter && !activeExt && !activeSize) {
          const statusNames = { A: "added", M: "modified", D: "deleted", R: "renamed" };
          emptyMsg = `No ${statusNames[activeStatus] || activeStatus} files in change.`;
        } else if (activeExt && !query && !isOwnerFilter && !isCommentsFilter && !activeStatus && !activeSize) {
          emptyMsg = `No ${escapeHtml(activeExt)} files in change.`;
        } else if (activeSize && !query && !isOwnerFilter && !isCommentsFilter && !activeStatus && !activeExt) {
          const sizeNames = { XS: "extra small (XS)", S: "small (S)", M: "medium (M)", L: "large (L)", XL: "extra large (XL)" };
          emptyMsg = `No ${sizeNames[activeSize] || activeSize} files in change.`;
        } else if (isHideRev && (visibleReviewedCount === 0 || hiddenReviewedCount === totalFiles) && !query && !isOwnerFilter && !isCommentsFilter && !activeStatus && !activeExt && !activeSize) {
          emptyMsg = `All ${totalFiles} files in change are reviewed ✓`;
        } else if (isCommentsFilter && isOwnerFilter) {
          emptyMsg = "No files with unresolved comments missing owner approval match filter.";
        } else if (isCommentsFilter) {
          emptyMsg = "No files have unresolved comments or drafts ✓";
        } else if (isOwnerFilter && query) {
          emptyMsg = `No files missing code owner approval match "${queryEscaped}".`;
        } else if (isOwnerFilter) {
          emptyMsg = "No files missing code owner approval ✓";
        }
        const clearBtnText = (isHideRev && !query && !isOwnerFilter && !isCommentsFilter && !activeStatus && !activeExt && !activeSize) ? "Show reviewed files (h)" : "Clear filters";
        const clearBtnAction = (isHideRev && !query && !isOwnerFilter && !isCommentsFilter && !activeStatus && !activeExt && !activeSize) ? `togglePgHideReviewed(${num}, false)` : `clearAllPgFileFilters(${num})`;
        emptyRow.innerHTML = `<td colspan="6" style="text-align: center; padding: 16px; color: var(--g-muted);">
          ${emptyMsg}
          <button type="button" class="pg-btn-subtle" onclick="${clearBtnAction}" style="margin-left: 8px;">${clearBtnText}</button>
        </td>`;
      }
    } else if (emptyRow) {
      emptyRow.style.display = "none";
    }

    const chipsWrap = changeView.querySelector(".pg-file-list-filter-chips");
    if (chipsWrap) {
      const queryChipHtml = query ? `<span class="pg-file-filter-chip">Filter: "${escapeHtml(query)}" <button type="button" class="pg-file-filter-chip-remove" onclick="clearPgFileFilter(${num})" title="Clear keyword filter">✕</button></span>` : "";
      const statusChipHtml = activeStatus ? `<span class="pg-file-filter-chip pg-filter-chip-status">Status: ${activeStatus === "A" ? "Added (A)" : (activeStatus === "D" ? "Deleted (D)" : (activeStatus === "R" ? "Renamed (R)" : "Modified (M)"))} <button type="button" class="pg-file-filter-chip-remove" onclick="setPgFileStatusFilter(${num}, '')" title="Clear status filter">✕</button></span>` : "";
      const extChipHtml = activeExt ? `<span class="pg-file-filter-chip pg-filter-chip-extension">Extension: ${escapeHtml(activeExt)} <button type="button" class="pg-file-filter-chip-remove" onclick="setPgFileExtensionFilter(${num}, '')" title="Clear extension filter">✕</button></span>` : "";
      const sizeChipHtml = activeSize ? `<span class="pg-file-filter-chip pg-filter-chip-size">Size: ${escapeHtml(activeSize)} <button type="button" class="pg-file-filter-chip-remove" onclick="setPgFileSizeFilter(${num}, '')" title="Clear size filter">✕</button></span>` : "";
      const ownerChipHtml = isOwnerFilter ? `<span class="pg-file-filter-chip">Missing owner approval <button type="button" class="pg-file-filter-chip-remove" onclick="togglePgOwnersFilter(${num})" title="Clear owner filter">✕</button></span>` : "";
      const commentsChipHtml = isCommentsFilter ? `<span class="pg-file-filter-chip">Unresolved / drafts only <button type="button" class="pg-file-filter-chip-remove" onclick="togglePgCommentsFilter(${num})" title="Clear comments filter">✕</button></span>` : "";
      const hideRevChipHtml = (isHideRev && hiddenReviewedCount > 0)
        ? `<span class="pg-file-filter-chip pg-filter-chip-reviewed" title="${hiddenReviewedCount} reviewed files hidden from list">Hide reviewed (${hiddenReviewedCount} hidden) <button type="button" class="pg-file-filter-chip-remove" onclick="togglePgHideReviewed(${num}, false)" title="Show reviewed files">✕</button></span>`
        : "";
      chipsWrap.innerHTML = queryChipHtml + statusChipHtml + extChipHtml + sizeChipHtml + ownerChipHtml + commentsChipHtml + hideRevChipHtml;
    }

    const filterBar = changeView.querySelector(".pg-file-list-filter-bar");
    if (filterBar) {
      if (hasAnyFilter) {
        filterBar.style.display = "";
        const textEl = filterBar.querySelector(".pg-filter-bar-text");
        if (textEl) {
          let text = "";
          if (isCommentsFilter && isOwnerFilter && query) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (with unresolved comments/drafts, missing owner approval and matching "<strong>${escapeHtml(query)}</strong>")`;
          } else if (isCommentsFilter && query) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (with unresolved comments/drafts and matching "<strong>${escapeHtml(query)}</strong>")`;
          } else if (isCommentsFilter && isOwnerFilter) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (with unresolved comments/drafts and missing code owner approval)`;
          } else if (isCommentsFilter) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files with unresolved comments or drafts`;
          } else if (isOwnerFilter && query) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (missing owner approval and matching "<strong>${escapeHtml(query)}</strong>")`;
          } else if (isOwnerFilter) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files missing code owner approval`;
          } else if (query) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files matching "<strong>${escapeHtml(query)}</strong>"`;
          } else if (isHideRev && hiddenReviewedCount > 0) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (<strong>${hiddenReviewedCount}</strong> reviewed hidden)`;
          }
          if (text && isHideRev && hiddenReviewedCount > 0 && (query || isOwnerFilter || isCommentsFilter)) {
            text += ` · <em>${hiddenReviewedCount} reviewed hidden</em>`;
          }
          if (text && activeStatus) {
            const statusNames = { A: "added files", M: "modified files", D: "deleted files", R: "renamed files" };
            text += ` · <em>${statusNames[activeStatus] || activeStatus}</em>`;
          } else if (!text && activeStatus) {
            const statusNames = { A: "Added (+)", M: "Modified (M)", D: "Deleted (-)", R: "Renamed (→)" };
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (${statusNames[activeStatus] || activeStatus})`;
          }
          if (text && activeExt) {
            text += ` · <em>${escapeHtml(activeExt)}</em>`;
          } else if (!text && activeExt) {
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (${escapeHtml(activeExt)})`;
          }
          if (text && activeSize) {
            const sizeNames = { XS: "extra small (XS)", S: "small (S)", M: "medium (M)", L: "large (L)", XL: "extra large (XL)" };
            text += ` · <em>${sizeNames[activeSize] || activeSize} files</em>`;
          } else if (!text && activeSize) {
            const sizeNames = { XS: "Extra small (XS)", S: "Small (S)", M: "Medium (M)", L: "Large (L)", XL: "Extra large (XL)" };
            text = `Showing <strong>${visibleCount}</strong> of <strong>${totalFiles}</strong> files (${sizeNames[activeSize] || activeSize})`;
          }
          textEl.innerHTML = text;
        }
      } else {
        filterBar.style.display = "none";
      }
    }

    const ownerBtn = document.getElementById(`btnFilterMissingOwners-${num}`);
    if (ownerBtn) {
      if (isOwnerFilter) {
        ownerBtn.classList.add("active");
      } else {
        ownerBtn.classList.remove("active");
      }
    }

    const commentsBtn = document.getElementById(`btnFilterComments-${num}`);
    if (commentsBtn) {
      if (isCommentsFilter) {
        commentsBtn.classList.add("active");
      } else {
        commentsBtn.classList.remove("active");
      }
    }

    const unreviewedBtn = document.getElementById(`btnFilterUnreviewed-${num}`);
    if (unreviewedBtn) {
      if (isHideRev) {
        unreviewedBtn.classList.add("active");
      } else {
        unreviewedBtn.classList.remove("active");
      }
      const totalUnrev = Math.max(0, totalFiles - (visibleReviewedCount + hiddenReviewedCount));
      if (totalUnrev === 0) {
        unreviewedBtn.innerHTML = "👁️ All reviewed ✓";
        unreviewedBtn.title = "All files marked as reviewed · Click to toggle unreviewed filter (Shortcut: h)";
      } else {
        unreviewedBtn.innerHTML = `👁️ Unreviewed (${totalUnrev})`;
        unreviewedBtn.title = `Filter files: show only unreviewed files (${totalUnrev} unreviewed) (Shortcut: h)`;
      }
    }

    const presetAllBtn = document.getElementById(`btnFilterPresetAll-${num}`);
    if (presetAllBtn) {
      presetAllBtn.classList.toggle("is-active", !hasAnyFilter);
    }
    const presetUnrevBtn = document.getElementById(`btnFilterPresetUnreviewed-${num}`);
    if (presetUnrevBtn) {
      presetUnrevBtn.classList.toggle("is-active", Boolean(isHideRev));
      const totalUnrev = Math.max(0, totalFiles - (visibleReviewedCount + hiddenReviewedCount));
      presetUnrevBtn.textContent = `Unreviewed (${totalUnrev})`;
    }
    const presetAddedBtn = document.getElementById(`btnFilterPresetAdded-${num}`);
    if (presetAddedBtn) {
      presetAddedBtn.classList.toggle("is-active", activeStatus === "A");
    }
    const presetModBtn = document.getElementById(`btnFilterPresetModified-${num}`);
    if (presetModBtn) {
      presetModBtn.classList.toggle("is-active", activeStatus === "M");
    }
    const presetDelBtn = document.getElementById(`btnFilterPresetDeleted-${num}`);
    if (presetDelBtn) {
      presetDelBtn.classList.toggle("is-active", activeStatus === "D");
    }
    const presetRenBtn = document.getElementById(`btnFilterPresetRenamed-${num}`);
    if (presetRenBtn) {
      presetRenBtn.classList.toggle("is-active", activeStatus === "R");
    }
    const presetUnresolvedBtn = document.getElementById(`btnFilterPresetUnresolved-${num}`);
    if (presetUnresolvedBtn) {
      presetUnresolvedBtn.classList.toggle("is-active", Boolean(isCommentsFilter));
    }
    const presetOwnersBtn = document.getElementById(`btnFilterPresetOwners-${num}`);
    if (presetOwnersBtn) {
      presetOwnersBtn.classList.toggle("is-active", Boolean(isOwnerFilter));
    }

    const statusBadges = changeView.querySelectorAll(".pg-file-status-badge");
    statusBadges.forEach(b => {
      const bTxt = b.textContent.trim().toUpperCase();
      b.classList.toggle("is-active-filter", Boolean(activeStatus && bTxt === activeStatus));
    });

    const extPresetBtns = changeView.querySelectorAll(`.pg-ext-preset-btn[data-cl="${num}"]`);
    extPresetBtns.forEach(btn => {
      const btnExt = btn.getAttribute("data-ext");
      btn.classList.toggle("is-active", Boolean(activeExt && btnExt === activeExt));
    });

    const extBadges = changeView.querySelectorAll(".pg-file-ext-badge");
    extBadges.forEach(b => {
      const bExt = b.getAttribute("data-ext");
      b.classList.toggle("is-active-filter", Boolean(activeExt && bExt === activeExt));
    });

    ["XS", "S", "M", "L", "XL"].forEach(cat => {
      const btn = document.getElementById(`btnFilterPresetSize${cat}-${num}`);
      if (btn) {
        btn.classList.toggle("is-active", Boolean(activeSize && activeSize === cat));
      }
    });

    const sizeBadges = changeView.querySelectorAll(".pg-file-size-badge");
    sizeBadges.forEach(b => {
      const bCat = (b.getAttribute("data-size") || b.textContent || "").trim().toUpperCase();
      b.classList.toggle("is-active-filter", Boolean(activeSize && bCat === activeSize));
    });

    if (state.focusedFileByCl && state.focusedFileByCl[num]) {
      const curFocused = state.focusedFileByCl[num];
      const curRow = rows.find(r => r.getAttribute("data-file") === curFocused);
      if (curRow && curRow.style.display === "none") {
        const firstVis = rows.find(r => r.style.display !== "none" && !r.classList.contains("pg-magic-file-row"));
        if (firstVis) {
          const newFile = firstVis.getAttribute("data-file");
          if (newFile && typeof focusPgFileRow === "function") {
            focusPgFileRow(num, newFile, { scrollIntoView: false });
          }
        }
      }
    }

    const filesTab = document.getElementById(`pgTabFiles-${num}`);
    if (filesTab) {
      const countEl = filesTab.querySelector(".pg-tab-count");
      if (countEl) {
        countEl.textContent = `(${hasAnyFilter ? `${visibleCount}/${totalFiles}` : totalFiles})`;
      }
    }

    const revSummaryEl = document.getElementById(`pgReviewedSummary-${num}`);
    if (revSummaryEl) {
      const isAllRev = visibleCount > 0 && visibleReviewedCount === visibleCount;
      revSummaryEl.className = `pg-reviewed-summary-badge is-clickable ${isAllRev ? "all-reviewed" : ""}`;
      if (isAllRev) {
        revSummaryEl.textContent = `All ${visibleCount} files reviewed ✓`;
      } else if (query || isOwnerFilter || isCommentsFilter) {
        revSummaryEl.textContent = `${visibleReviewedCount} / ${visibleCount} filtered reviewed`;
      } else {
        revSummaryEl.textContent = `${visibleReviewedCount} / ${visibleCount} reviewed`;
      }
    }
    syncPgMasterReviewedCheckbox(num, curPs);
    syncPgMarkAllReviewedButton(num, curPs);
  }

  function renderPgOwnersSummaryHtml(c, bundle) {
    const fileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (fileList.length === 0) return "";
    const isFiltered = isPgOwnerFilterActive(c.number);
    const revDetail = Array.isArray(c && c.reviewers_detail) ? c.reviewers_detail : [];
    const vCr = c.viewer_cr_vote || 0;
    const hCr2 = c.human_cr2_count || 0;
    const rLen = revDetail.length;
    const r0 = rLen > 0 ? revDetail[0] : null;
    const r1 = rLen > 1 ? revDetail[1] : null;
    const r0Em = r0 ? (r0.email || r0.name || "") : "";
    const r0Vote = r0 ? (r0.cr_vote || 0) : 0;
    const r1Em = r1 ? (r1.email || r1.name || "") : "";
    const r1Vote = r1 ? (r1.cr_vote || 0) : 0;
    if (bundle && rLen <= 2 && typeof bundle._cachedOwnersSummaryHtml === "string") {
      const osSub = bundle._ownersSumSub;
      if (
        osSub &&
        osSub.num === c.number &&
        osSub.fileLen === fileList.length &&
        osSub.isFiltered === Boolean(isFiltered) &&
        osSub.vCr === vCr &&
        osSub.hCr2 === hCr2 &&
        osSub.rLen === rLen &&
        osSub.r0Em === r0Em &&
        osSub.r0Vote === r0Vote &&
        osSub.r1Em === r1Em &&
        osSub.r1Vote === r1Vote
      ) {
        return bundle._cachedOwnersSummaryHtml;
      }
    }
    const revVoteSig = rLen === 0
      ? ""
      : revDetail.map(r => r ? `${r.email || r.name || ""}:${r.cr_vote || 0}` : "").join(",");
    const cacheKey = `${c.number}|${fileList.length}|${isFiltered ? 1 : 0}|${vCr}|${hCr2}|${revVoteSig}`;
    if (bundle && bundle._cachedOwnersSummaryKey === cacheKey && typeof bundle._cachedOwnersSummaryHtml === "string") {
      if (rLen <= 2) {
        bundle._ownersSumSub = { num: c.number, fileLen: fileList.length, isFiltered: Boolean(isFiltered), vCr, hCr2, rLen, r0Em, r0Vote, r1Em, r1Vote };
      }
      return bundle._cachedOwnersSummaryHtml;
    }
    let missingCount = 0;
    const clOwnerPrefix = computePgClOwnerStatusPrefix(c);
    for (let i = 0; i < fileList.length; i++) {
      const fi = fileList[i];
      const ownInfo = (
        fi._loopOwnerInfo &&
        fi._loopOwnerClPrefix === clOwnerPrefix &&
        fi._loopOwnerRawSt === fi.owner_status &&
        fi._loopOwnerRawApp === fi.owner_approved &&
        (!Array.isArray(fi.owners) || fi.owners.length === 0)
      )
        ? fi._loopOwnerInfo
        : getPgFileOwnerStatus(c, fi, clOwnerPrefix);
      if (ownInfo.status === "missing") {
        missingCount++;
      }
    }
    const isAllApproved = missingCount === 0;

    const html = isAllApproved
      ? `<span id="pgOwnersSummary-${c.number}" class="pg-owners-summary-badge all-approved" title="All files have been approved by code owners">
        <span class="pg-owners-summary-icon">✓</span> All files approved
      </span>`
      : `<span id="pgOwnersSummary-${c.number}" class="pg-owners-summary-badge has-missing">
      <button type="button" id="btnFilterMissingOwners-${c.number}" class="pg-btn-subtle pg-owners-filter-btn ${isFiltered ? "active" : ""}" onclick="togglePgOwnersFilter(${c.number})" title="Filter files by code owner approval status (Shortcut: Shift+O)">
        <span class="pg-owners-summary-icon">✕</span> ${missingCount} missing owner approval ${isFiltered ? "(Filtered)" : "(Filter)"}
      </button>
    </span>`;
    if (bundle) {
      bundle._cachedOwnersSummaryKey = cacheKey;
      bundle._cachedOwnersSummaryHtml = html;
      if (rLen <= 2) {
        bundle._ownersSumSub = { num: c.number, fileLen: fileList.length, isFiltered: Boolean(isFiltered), vCr, hCr2, rLen, r0Em, r0Vote, r1Em, r1Vote };
      }
    }
    return html;
  }

  function getPgDeltaSizeTier(total) {
    if (total >= PG_SIZE_L_MAX) return { category: "XL", name: "Extra large" };
    if (total >= PG_SIZE_M_MAX) return { category: "L", name: "Large" };
    if (total >= PG_SIZE_S_MAX) return { category: "M", name: "Medium" };
    if (total >= PG_SIZE_XS_MAX) return { category: "S", name: "Small" };
    if (total >= 1) return { category: "XS", name: "Extra small" };
    return { category: "", name: "No line modifications" };
  }

  function getPgFileSizeInfo(f) {
    const adds = Math.max(0, Number((f && f.additions) || 0));
    const dels = Math.max(0, Number((f && f.deletions) || 0));
    if (f && f._cachedSizeInfo && f._cachedSizeAdds === adds && f._cachedSizeDels === dels) {
      return f._cachedSizeInfo;
    }
    const total = adds + dels;
    const tier = getPgDeltaSizeTier(total);
    const totalStr = total < 1000 ? String(total) : total.toLocaleString();
    const label = tier.category
      ? `${tier.name} (${totalStr} lines: +${adds}, -${dels})`
      : tier.name;
    const res = { additions: adds, deletions: dels, total, category: tier.category, label };
    if (f && typeof f === "object") {
      f._cachedSizeAdds = adds;
      f._cachedSizeDels = dels;
      f._cachedSizeInfo = res;
    }
    return res;
  }

  function renderPgFileSizeBadgeHtml(f, clNum, precomputedInfo = null) {
    const info = precomputedInfo || getPgFileSizeInfo(f);
    if (!info.category) {
      return `<span class="pg-file-size-badge size-none" title="No line modifications or binary file">—</span>`;
    }
    const num = Number(clNum || state.selectedClNumber || 0);
    const activeSize = (num && typeof getPgFileSizeFilter === "function") ? getPgFileSizeFilter(num) : "";
    const isActive = Boolean(activeSize && activeSize === info.category);
    const cls = `size-${info.category.toLowerCase()}`;
    const clickAttr = num
      ? ` onclick="onPgFileSizeBadgeClick(event, ${num}, '${info.category}')" onkeydown="onPgFileSizeBadgeKeyDown(event, ${num}, '${info.category}')"`
      : "";
    return `<span class="pg-file-size-badge ${cls} is-clickable${isActive ? " is-active-filter" : ""}" role="button" tabindex="0" data-size="${info.category}"${clickAttr} title="Filter by size: ${info.label} · Click to toggle (Alt+B)" aria-label="Size: ${info.label}">${info.category}</span>`;
  }

  function renderPgFileDeltaNumbersHtml(addsInput, delsInput) {
    const adds = Math.max(0, Number(addsInput || 0));
    const dels = Math.max(0, Number(delsInput || 0));
    if (adds === 0 && dels === 0) {
      return `<span class="pg-file-delta pg-delta-zero" title="No line modifications">0</span>`;
    }
    if (adds > 0 && dels > 0) {
      return `<span class="pg-file-delta"><span class="pg-delta-add">+${adds}</span>, <span class="pg-delta-del">-${dels}</span></span>`;
    }
    if (adds > 0) {
      return `<span class="pg-file-delta"><span class="pg-delta-add">+${adds}</span></span>`;
    }
    return `<span class="pg-file-delta"><span class="pg-delta-del">-${dels}</span></span>`;
  }

  function renderPgFileDeltaBarHtml(f, maxDelta = null) {
    const adds = Math.max(0, Number((f && f.additions) || 0));
    const dels = Math.max(0, Number((f && f.deletions) || 0));
    const total = adds + dels;
    if (total === 0) {
      return `<span class="pg-delta-bar is-empty" title="No line modifications"></span>`;
    }
    const addPct = Math.round((adds / total) * 100);
    const delPct = 100 - addPct;
    const mDelta = (maxDelta !== null && maxDelta !== undefined) ? Number(maxDelta) : 0;
    const fillPct = mDelta > 0 ? Math.min(100, Math.max(8, Math.round((total / mDelta) * 100))) : 100;
    const totalStr = total < 1000 ? String(total) : total.toLocaleString();
    return `<span class="pg-delta-bar" title="+${adds}, -${dels} (${totalStr} lines changed)"><span class="pg-delta-bar-fill" style="width: ${fillPct}%;"><span class="pg-delta-bar-add" style="width: ${addPct}%;"></span><span class="pg-delta-bar-del" style="width: ${delPct}%;"></span></span></span>`;
  }

  function getPgFileSort(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (state.pgFileSortByCl && state.pgFileSortByCl[num]) {
      return state.pgFileSortByCl[num];
    }
    try {
      if (typeof localStorage === "undefined") return { col: "path", dir: "asc" };
      const stored = localStorage.getItem("gerrit_ui_pg_file_sort");
      if (!stored) return { col: "path", dir: "asc" };
      const parsed = JSON.parse(stored);
      if (parsed && parsed.col) {
        state.pgFileSortByCl = state.pgFileSortByCl || {};
        state.pgFileSortByCl[num] = parsed;
        return parsed;
      }
    } catch (_) {}
    return { col: "path", dir: "asc" };
  }

  function setPgFileSort(clNum, col, dir) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    if (!state.pgFileSortByCl) state.pgFileSortByCl = {};
    state.pgFileSortByCl[num] = {
      col: col || "path",
      dir: dir === "desc" ? "desc" : "asc",
    };
    try {
      if (typeof localStorage !== "undefined") {
        localStorage.setItem("gerrit_ui_pg_file_sort", JSON.stringify(state.pgFileSortByCl[num]));
      }
    } catch (_) {}
  }

  function comparePgFilePaths(a, b) {
    const fa = String((a && a.file) || "");
    const fb = String((b && b.file) || "");
    if (fa === fb) return 0;
    let la = a && a._cachedRawFile === fa ? a._cachedLowerFile : "";
    if (!la && fa) {
      la = fa.toLowerCase();
      if (a) {
        a._cachedRawFile = fa;
        a._cachedLowerFile = la;
      }
    }
    let lb = b && b._cachedRawFile === fb ? b._cachedLowerFile : "";
    if (!lb && fb) {
      lb = fb.toLowerCase();
      if (b) {
        b._cachedRawFile = fb;
        b._cachedLowerFile = lb;
      }
    }
    if (la < lb) return -1;
    if (la > lb) return 1;
    return fa < fb ? -1 : 1;
  }

  function getPgSortedFileList(c, rawFileList) {
    if (!Array.isArray(rawFileList) || rawFileList.length <= 1) {
      return rawFileList ? [...rawFileList] : [];
    }
    const num = Number((c && c.number) || state.selectedClNumber || 0);
    const sort = getPgFileSort(num);
    const col = sort.col || "path";
    const dir = sort.dir === "desc" ? "desc" : "asc";
    const multiplier = dir === "desc" ? -1 : 1;

    const isStaticSortCol = col === "path" || col === "delta" || col === "size" || col === "status";
    let staticSortSig = "";
    if (isStaticSortCol) {
      const f0 = rawFileList[0];
      const fLast = rawFileList[rawFileList.length - 1];
      const sSub = rawFileList._sortSub;
      if (
        sSub &&
        Array.isArray(rawFileList._cachedSortedFiles) &&
        rawFileList._cachedSortedFiles.length === rawFileList.length &&
        sSub.col === col &&
        sSub.dir === dir &&
        sSub.len === rawFileList.length &&
        sSub.f0File === (f0 ? f0.file : undefined) &&
        sSub.f0Add === (f0 ? f0.additions : undefined) &&
        sSub.f0Del === (f0 ? f0.deletions : undefined) &&
        sSub.f0Status === (f0 ? f0.status : undefined) &&
        sSub.fLastFile === (fLast ? fLast.file : undefined) &&
        sSub.fLastAdd === (fLast ? fLast.additions : undefined) &&
        sSub.fLastDel === (fLast ? fLast.deletions : undefined) &&
        sSub.fLastStatus === (fLast ? fLast.status : undefined)
      ) {
        return rawFileList._cachedSortedFiles;
      }
      staticSortSig = `${col}:${dir}:${rawFileList.length}:${f0 ? (f0.file || "") + ":" + (f0.additions || 0) + ":" + (f0.deletions || 0) + ":" + (f0.status || "") : ""}:${fLast ? (fLast.file || "") + ":" + (fLast.additions || 0) + ":" + (fLast.deletions || 0) + ":" + (fLast.status || "") : ""}`;
      if (rawFileList._cachedSortedSig === staticSortSig && Array.isArray(rawFileList._cachedSortedFiles) && rawFileList._cachedSortedFiles.length === rawFileList.length) {
        return rawFileList._cachedSortedFiles;
      }
    }

    const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[num]) || {};
    const curPs = Number(selPsMeta.ps || (c && c.patch_set) || 1);
    const revSet = getPgReviewedSet(num, curPs);

    const statusPriority = { A: 1, M: 2, R: 3, D: 4 };

    const sorted = [...rawFileList].sort((a, b) => {
      if (a.file === "/COMMIT_MSG") return -1;
      if (b.file === "/COMMIT_MSG") return 1;

      let res = 0;
      if (col === "path") {
        res = comparePgFilePaths(a, b);
      } else if (col === "delta" || col === "size") {
        const deltaA = Math.max(0, Number(a.additions || 0)) + Math.max(0, Number(a.deletions || 0));
        const deltaB = Math.max(0, Number(b.additions || 0)) + Math.max(0, Number(b.deletions || 0));
        res = deltaA - deltaB;
      } else if (col === "status") {
        const stA = getPgFileStatus(a);
        const stB = getPgFileStatus(b);
        const rankA = statusPriority[stA] || 99;
        const rankB = statusPriority[stB] || 99;
        res = rankA - rankB;
      } else if (col === "comments") {
        const cmA = getInlineCommentsForFile(num, a.file);
        const cmB = getInlineCommentsForFile(num, b.file);
        const unresA = cmA.filter(m => m.unresolved !== false).length;
        const unresB = cmB.filter(m => m.unresolved !== false).length;
        if (unresA !== unresB) {
          res = unresA - unresB;
        } else {
          res = cmA.length - cmB.length;
        }
      } else if (col === "owners") {
        const ownA = getPgFileOwnerStatus(c, a).status;
        const ownB = getPgFileOwnerStatus(c, b).status;
        const rankOwner = { missing: 1, approved: 2, na: 3 };
        const rankA = rankOwner[ownA] || 99;
        const rankB = rankOwner[ownB] || 99;
        res = rankA - rankB;
      } else if (col === "reviewed") {
        const isRevA = revSet && revSet.has(a.file) ? 1 : 0;
        const isRevB = revSet && revSet.has(b.file) ? 1 : 0;
        res = isRevA - isRevB;
      }

      if (res !== 0) {
        return res * multiplier;
      }
      return comparePgFilePaths(a, b);
    });

    if (isStaticSortCol) {
      const f0 = rawFileList[0];
      const fLast = rawFileList[rawFileList.length - 1];
      rawFileList._cachedSortedSig = staticSortSig;
      rawFileList._cachedSortedFiles = sorted;
      rawFileList._sortSub = {
        col,
        dir,
        len: rawFileList.length,
        f0File: f0 ? f0.file : undefined,
        f0Add: f0 ? f0.additions : undefined,
        f0Del: f0 ? f0.deletions : undefined,
        f0Status: f0 ? f0.status : undefined,
        fLastFile: fLast ? fLast.file : undefined,
        fLastAdd: fLast ? fLast.additions : undefined,
        fLastDel: fLast ? fLast.deletions : undefined,
        fLastStatus: fLast ? fLast.status : undefined,
      };
    }

    return sorted;
  }

  function togglePgFileSort(clNum, col) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const current = getPgFileSort(num);
    let newDir = "asc";
    if (current.col === col) {
      newDir = current.dir === "asc" ? "desc" : "asc";
    } else {
      newDir = (col === "delta" || col === "size" || col === "comments") ? "desc" : "asc";
    }
    setPgFileSort(num, col, newDir);
    renderDiffPane();
    if (typeof showToast === "function") {
      const colLabels = {
        path: "Path",
        delta: "Delta / Size",
        size: "Delta / Size",
        comments: "Comments",
        owners: "Owners",
        status: "Status",
        reviewed: "Reviewed",
      };
      const colName = colLabels[col] || col;
      const dirName = newDir === "asc" ? "ascending ▲" : "descending ▼";
      showToast(`Sorted by ${colName} (${dirName})`);
    }
  }

  function resetPgFileSort(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    setPgFileSort(num, "path", "asc");
    renderDiffPane();
    if (typeof showToast === "function") {
      showToast("File list sort reset to Path (A → Z) ✓");
    }
  }

  function onPgFileSortSelectChange(clNum, val) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num || !val) return;
    const parts = val.split(":");
    const col = parts[0] || "path";
    const dir = parts[1] || "asc";
    setPgFileSort(num, col, dir);
    renderDiffPane();
    if (typeof syncPgRelationSlots === "function") {
      syncPgRelationSlots();
    }
  }

  function cyclePgFileSort(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const cycleOrder = [
      { col: "path", dir: "asc", label: "Path (A → Z)" },
      { col: "delta", dir: "desc", label: "Delta / Size (Largest first)" },
      { col: "comments", dir: "desc", label: "Comments (Most comments first)" },
      { col: "owners", dir: "asc", label: "Owners (Missing approval first)" },
      { col: "status", dir: "asc", label: "Status (Added → Modified → Deleted)" },
      { col: "reviewed", dir: "asc", label: "Reviewed (Unreviewed first)" },
    ];
    const current = getPgFileSort(num);
    let nextIdx = 0;
    const currentIdx = cycleOrder.findIndex(s => s.col === current.col && s.dir === current.dir);
    if (currentIdx !== -1) {
      nextIdx = (currentIdx + 1) % cycleOrder.length;
    }
    const nextSort = cycleOrder[nextIdx];
    setPgFileSort(num, nextSort.col, nextSort.dir);
    renderDiffPane();
    if (typeof showToast === "function") {
      showToast(`Sorted by ${nextSort.label} ⇅`);
    }
  }

  function getPgFileTreeMode(clNum) {
    const num = Number(clNum || 0);
    if (!state.fileTreeModeByCl) state.fileTreeModeByCl = {};
    if (typeof state.fileTreeModeByCl[num] === "boolean") {
      return state.fileTreeModeByCl[num];
    }
    try {
      const stored = localStorage.getItem("gerrit_ui_pg_file_tree_mode");
      if (stored === "tree") return true;
      if (stored === "flat") return false;
    } catch (_) {}
    return false;
  }

  function setPgFileTreeMode(clNum, isTree) {
    const num = Number(clNum || 0);
    if (!state.fileTreeModeByCl) state.fileTreeModeByCl = {};
    state.fileTreeModeByCl[num] = Boolean(isTree);
    try {
      localStorage.setItem("gerrit_ui_pg_file_tree_mode", isTree ? "tree" : "flat");
    } catch (_) {}
    renderDiffPane();
    if (typeof showToast === "function") {
      showToast(isTree ? "Switched to Tree view 🌳 (y)" : "Switched to Flat list 📋 (y)");
    }
  }

  function togglePgFileTreeMode(clNum) {
    const num = Number(clNum || state.selectedClNumber || 0);
    if (!num) return;
    const current = getPgFileTreeMode(num);
    setPgFileTreeMode(num, !current);
  }

  function getPgDirRowEl(container, dir) {
    if (!container || !dir) return null;
    const rows = container.querySelectorAll(".pg-dir-row");
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute("data-dir") === dir) return rows[i];
    }
    return null;
  }

  function getPgChildFileRows(container, dir) {
    if (!container || !dir) return [];
    const rows = container.querySelectorAll(".pg-file-row");
    const result = [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute("data-dir") === dir) result.push(rows[i]);
    }
    return result;
  }

  const PG_COLLAPSED_DIRS_STORAGE_PREFIX = "gerrit_ui_pg_collapsed_dirs_";

  function getPgCollapsedDirs(clNum) {
    const num = Number(clNum || 0);
    if (!state.collapsedDirsByCl) state.collapsedDirsByCl = {};
    if (!state.collapsedDirsByCl[num]) {
      state.collapsedDirsByCl[num] = new Set();
      let stored = null;
      try {
        if (typeof localStorage !== "undefined") {
          stored = localStorage.getItem(`${PG_COLLAPSED_DIRS_STORAGE_PREFIX}${num}`);
        }
      } catch (_) {}
      if (!stored) {
        try {
          if (typeof sessionStorage !== "undefined") {
            stored = sessionStorage.getItem(`${PG_COLLAPSED_DIRS_STORAGE_PREFIX}${num}`);
          }
        } catch (_) {}
      }
      if (stored) {
        try {
          const arr = JSON.parse(stored);
          if (Array.isArray(arr)) {
            state.collapsedDirsByCl[num] = new Set(arr);
          }
        } catch (_) {}
      }
    }
    return state.collapsedDirsByCl[num];
  }

  function savePgCollapsedDirs(clNum) {
    const num = Number(clNum || 0);
    if (!num) return;
    try {
      const set = getPgCollapsedDirs(num);
      const serialized = JSON.stringify(Array.from(set));
      if (typeof localStorage !== "undefined") {
        localStorage.setItem(`${PG_COLLAPSED_DIRS_STORAGE_PREFIX}${num}`, serialized);
      }
      if (typeof sessionStorage !== "undefined") {
        sessionStorage.setItem(`${PG_COLLAPSED_DIRS_STORAGE_PREFIX}${num}`, serialized);
      }
    } catch (_) {}
  }

  function copyPgDirPath(event, dirPath) {
    if (event) {
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      if (typeof event.preventDefault === "function") event.preventDefault();
    }
    const pathStr = String(dirPath || "").trim();
    if (!pathStr) return;
    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(pathStr).catch(() => {});
    }
    if (typeof showToast === "function") {
      showToast(`Copied directory "${pathStr}" to clipboard ✓`);
    }
  }

  function onPgDirRowKeyDown(event, clNum, dir) {
    if (!event) return;
    const key = event.key;
    if (event.altKey && (key === "o" || key === "O")) {
      event.preventDefault();
      openPgCodeOwnersDialog(clNum, dir, true);
      return;
    }
    if (key === "Enter" || key === " ") {
      event.preventDefault();
      togglePgDirCollapse(clNum, dir);
      return;
    }
    const num = Number(clNum || 0);
    const collapsedSet = getPgCollapsedDirs(num);
    const isCollapsed = collapsedSet.has(dir);
    if (key === "ArrowLeft") {
      if (!isCollapsed) {
        event.preventDefault();
        togglePgDirCollapse(clNum, dir);
      }
      return;
    }
    if (key === "ArrowRight") {
      if (isCollapsed) {
        event.preventDefault();
        togglePgDirCollapse(clNum, dir);
      } else {
        const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
        if (changeView) {
          const firstChild = getPgChildFileRows(changeView, dir)[0];
          if (firstChild) {
            event.preventDefault();
            const filePath = firstChild.getAttribute("data-file");
            if (filePath) {
              focusPgFileRow(num, filePath, { scrollIntoView: true, focusElement: true });
              if (typeof firstChild.focus === "function") firstChild.focus();
            }
          }
        }
      }
      return;
    }
    if (key === "ArrowDown" || key === "j" || key === "J") {
      const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
      if (changeView) {
        const visibleRows = Array.from(changeView.querySelectorAll(".pg-file-row, .pg-dir-row")).filter(r => r.style.display !== "none");
        const curRow = event.currentTarget || event.target;
        const idx = visibleRows.indexOf(curRow);
        if (idx !== -1 && idx < visibleRows.length - 1) {
          event.preventDefault();
          const nextRow = visibleRows[idx + 1];
          if (nextRow) {
            if (nextRow.classList.contains("pg-file-row")) {
              const f = nextRow.getAttribute("data-file");
              if (f) focusPgFileRow(num, f, { scrollIntoView: true, focusElement: true });
            } else if (typeof nextRow.focus === "function") {
              nextRow.focus();
            }
          }
        }
      }
      return;
    }
    if (key === "ArrowUp" || key === "k" || key === "K") {
      const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
      if (changeView) {
        const visibleRows = Array.from(changeView.querySelectorAll(".pg-file-row, .pg-dir-row")).filter(r => r.style.display !== "none");
        const curRow = event.currentTarget || event.target;
        const idx = visibleRows.indexOf(curRow);
        if (idx > 0) {
          event.preventDefault();
          const prevRow = visibleRows[idx - 1];
          if (prevRow) {
            if (prevRow.classList.contains("pg-file-row")) {
              const f = prevRow.getAttribute("data-file");
              if (f) focusPgFileRow(num, f, { scrollIntoView: true, focusElement: true });
            } else if (typeof prevRow.focus === "function") {
              prevRow.focus();
            }
          }
        }
      }
      return;
    }
    if (key === "p" || key === "P") {
      event.preventDefault();
      if (event.shiftKey || key === "P") {
        copyPgFilePaths(num, "visible", null, dir);
      } else {
        copyPgDirPath(event, dir);
      }
      return;
    }
  }

  function togglePgDirCollapse(clNum, dir) {
    const num = Number(clNum || 0);
    const collapsedSet = getPgCollapsedDirs(num);
    if (collapsedSet.has(dir)) {
      collapsedSet.delete(dir);
    } else {
      collapsedSet.add(dir);
    }
    savePgCollapsedDirs(num);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;
    const dirRow = getPgDirRowEl(changeView, dir);
    const isCollapsed = collapsedSet.has(dir);
    if (dirRow) {
      if (isCollapsed) dirRow.classList.add("is-collapsed");
      else dirRow.classList.remove("is-collapsed");
      dirRow.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
      const chevron = dirRow.querySelector(".pg-dir-chevron");
      if (chevron) chevron.textContent = isCollapsed ? "▶" : "▼";
      const toggleBtn = dirRow.querySelector(".pg-dir-toggle-btn");
      if (toggleBtn) {
        toggleBtn.title = isCollapsed ? "Expand directory (Enter/Space)" : "Collapse directory (Enter/Space)";
      }
    }
    const childRows = getPgChildFileRows(changeView, dir);
    childRows.forEach(r => {
      r.style.display = isCollapsed ? "none" : "";
    });
    syncPgToggleAllDirsButton(num);
  }

  function areAllPgDirsCollapsed(clNum) {
    const num = Number(clNum || 0);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (changeView) {
      const dirRows = Array.from(changeView.querySelectorAll(".pg-dir-row"));
      if (dirRows.length > 0) {
        return dirRows.every(r => r.classList.contains("is-collapsed"));
      }
    }
    const bundle = state.diffsByCl && state.diffsByCl[num];
    const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (!rawFiles.length) return false;
    const revSet = (typeof getPgReviewedSet === "function") ? getPgReviewedSet(num) : new Set();
    const c = (state.changes || []).find(ch => ch.number === num) || { number: num };
    const dirGroups = groupPgFilesByDirectory(rawFiles, revSet, c).filter(d => Boolean(d.dir));
    if (!dirGroups.length) return false;
    const collapsedSet = getPgCollapsedDirs(num);
    return dirGroups.every(d => collapsedSet.has(d.dir));
  }

  function toggleAllPgDirsCollapse(clNum) {
    const num = Number(clNum || 0);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    const allCollapsed = areAllPgDirsCollapsed(num);
    const shouldCollapse = !allCollapsed;
    const collapsedSet = getPgCollapsedDirs(num);

    if (changeView) {
      const dirRows = Array.from(changeView.querySelectorAll(".pg-dir-row"));
      dirRows.forEach(dirRow => {
        const dir = dirRow.getAttribute("data-dir");
        if (shouldCollapse) {
          collapsedSet.add(dir);
          dirRow.classList.add("is-collapsed");
          dirRow.setAttribute("aria-expanded", "false");
        } else {
          collapsedSet.delete(dir);
          dirRow.classList.remove("is-collapsed");
          dirRow.setAttribute("aria-expanded", "true");
        }
        const chevron = dirRow.querySelector(".pg-dir-chevron");
        if (chevron) chevron.textContent = shouldCollapse ? "▶" : "▼";
        const toggleBtn = dirRow.querySelector(".pg-dir-toggle-btn");
        if (toggleBtn) {
          toggleBtn.title = shouldCollapse ? "Expand directory (Enter/Space)" : "Collapse directory (Enter/Space)";
        }
        const childRows = getPgChildFileRows(changeView, dir);
        childRows.forEach(r => {
          r.style.display = shouldCollapse ? "none" : "";
        });
      });
    } else {
      const bundle = state.diffsByCl && state.diffsByCl[num];
      const rawFiles = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
      const revSet = (typeof getPgReviewedSet === "function") ? getPgReviewedSet(num) : new Set();
      const c = (state.changes || []).find(ch => ch.number === num) || { number: num };
      const dirGroups = groupPgFilesByDirectory(rawFiles, revSet, c).filter(d => Boolean(d.dir));
      dirGroups.forEach(d => {
        if (shouldCollapse) {
          collapsedSet.add(d.dir);
        } else {
          collapsedSet.delete(d.dir);
        }
      });
    }
    savePgCollapsedDirs(num);
    syncPgToggleAllDirsButton(num);
    if (typeof showToast === "function") {
      showToast(shouldCollapse ? "Collapsed all directories (Alt+Y)" : "Expanded all directories (Alt+Y)");
    }
  }

  function syncPgToggleAllDirsButton(clNum) {
    const num = Number(clNum || 0);
    const btn = document.getElementById(`btnToggleAllDirs-${num}`);
    if (!btn) return;
    const allCollapsed = areAllPgDirsCollapsed(num);
    if (allCollapsed) {
      btn.classList.add("is-all-collapsed");
      btn.textContent = "⤢ Expand dirs";
      btn.title = "Expand all directories in tree view (Alt+Y)";
    } else {
      btn.classList.remove("is-all-collapsed");
      btn.textContent = "⤡ Collapse dirs";
      btn.title = "Collapse all directories in tree view (Alt+Y)";
    }
  }

  function syncPgDirReviewedCheckbox(clNum, dir) {
    const num = Number(clNum || 0);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${num}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;
    const dirRow = getPgDirRowEl(changeView, dir);
    if (!dirRow) return;
    const childCbs = getPgChildFileRows(changeView, dir).map(r => r.querySelector(".pg-file-reviewed-cb")).filter(Boolean);
    if (childCbs.length === 0) return;
    const revCount = childCbs.filter(cb => cb.checked).length;
    const allRev = revCount === childCbs.length;
    const someRev = revCount > 0 && !allRev;
    const dirCb = dirRow.querySelector(".pg-dir-reviewed-cb");
    if (dirCb) {
      dirCb.checked = allRev;
      dirCb.indeterminate = someRev;
    }
    const textEl = dirRow.querySelector(".pg-dir-reviewed-text");
    if (textEl) {
      textEl.textContent = `${revCount}/${childCbs.length}`;
    }
  }

  function onPgDirReviewedCheckboxChanged(cbEl) {
    if (!cbEl) return;
    const clNum = Number(cbEl.getAttribute("data-cl"));
    const ps = Number(cbEl.getAttribute("data-ps") || 1);
    const dir = cbEl.getAttribute("data-dir") || "";
    const isChecked = Boolean(cbEl.checked);
    const changeView = document.querySelector(`.pg-change-view[data-cl="${clNum}"]`) || document.querySelector(".pg-change-view");
    if (!changeView) return;
    const childCbs = getPgChildFileRows(changeView, dir).map(r => r.querySelector(".pg-file-reviewed-cb")).filter(Boolean);
    childCbs.forEach(childCb => {
      const fPath = childCb.getAttribute("data-file");
      setPgFileReviewed(clNum, ps, fPath, isChecked);
      childCb.checked = isChecked;
      const row = childCb.closest(".pg-file-row");
      if (row) {
        if (isChecked) row.classList.add("is-reviewed");
        else row.classList.remove("is-reviewed");
      }
    });
    syncPgMasterReviewedCheckbox(clNum);
    syncPgMarkAllReviewedButton(clNum);
    renderDiffPane();
    if (typeof showToast === "function") {
      showToast(`${isChecked ? "✓ Marked" : "Unmarked"} all files in ${dir} as ${isChecked ? "reviewed" : "unreviewed"}`);
    }
  }

  function groupPgFilesByDirectory(fileList, revSet, c) {
    const dirMap = new Map();
    fileList.forEach(f => {
      const path = String(f.file || "");
      const lastSlash = path.lastIndexOf("/");
      const dir = lastSlash !== -1 ? path.substring(0, lastSlash + 1) : "";
      const basename = lastSlash !== -1 ? path.substring(lastSlash + 1) : path;
      if (!dirMap.has(dir)) {
        dirMap.set(dir, {
          dir,
          files: [],
          additions: 0,
          deletions: 0,
          commentsCount: 0,
          unresolvedCount: 0,
          resolvedCount: 0,
          draftCount: 0,
          robotCount: 0,
          hasUnres: false,
          hasDrafts: false,
          reviewedCount: 0,
          missingOwnerCount: 0,
        });
      }
      const entry = dirMap.get(dir);
      f.dir = dir;
      f.basename = basename;
      entry.files.push(f);
      entry.additions += Number(f.additions || 0);
      entry.deletions += Number(f.deletions || 0);
      const stats = (typeof getPgFileCommentStats === "function")
        ? getPgFileCommentStats(c.number, f.file)
        : { humanCount: 0, unresolvedCount: 0, resolvedCount: 0, draftCount: 0, robotCount: 0, hasUnresolved: false, hasDrafts: false };
      entry.commentsCount += stats.humanCount;
      entry.unresolvedCount += stats.unresolvedCount;
      entry.resolvedCount += stats.resolvedCount;
      entry.draftCount += stats.draftCount;
      entry.robotCount += stats.robotCount;
      if (stats.hasUnresolved) {
        entry.hasUnres = true;
      }
      if (stats.hasDrafts) {
        entry.hasDrafts = true;
      }
      const fileChecks = (typeof getPgFileChecksStatus === "function") ? getPgFileChecksStatus(c, f) : null;
      if (fileChecks && fileChecks.hasChecks) {
        entry.hasChecks = true;
        if (fileChecks.status === "failed") entry.failedChecksCount = (entry.failedChecksCount || 0) + 1;
        else if (fileChecks.status === "warning") entry.warningChecksCount = (entry.warningChecksCount || 0) + 1;
        else if (fileChecks.status === "running") entry.runningChecksCount = (entry.runningChecksCount || 0) + 1;
        else if (fileChecks.status === "passed") entry.passedChecksCount = (entry.passedChecksCount || 0) + 1;
      }
      if (revSet.has(f.file)) {
        entry.reviewedCount++;
      }
      const ownerStatus = getPgFileOwnerStatus(c, f);
      if (ownerStatus.status === "missing") {
        entry.missingOwnerCount++;
      }
    });
    return Array.from(dirMap.values());
  }

  function renderPgDirRowHtml(c, entry, curPs, isCollapsed, revSet, maxDelta = null) {
    const safeDir = escapeHtml(entry.dir);
    const isDirAllRev = entry.files.length > 0 && entry.reviewedCount === entry.files.length;
    const isDirSomeRev = entry.reviewedCount > 0 && !isDirAllRev;
    const dirBadges = [];
    if (entry.draftCount > 0) {
      dirBadges.push(`<span class="pg-file-draft-badge pg-dir-draft-badge" title="${entry.draftCount} draft comment(s) in this directory">📝 ${entry.draftCount}</span>`);
    }
    if (entry.unresolvedCount > 0) {
      dirBadges.push(`<span class="pg-file-comments-badge pg-file-unresolved-badge pg-dir-unresolved-badge has-unresolved" title="${entry.unresolvedCount} unresolved comment(s) in this directory">⚠️ ${entry.unresolvedCount} unres</span>`);
      if (entry.resolvedCount > 0) {
        dirBadges.push(`<span class="pg-file-comments-badge pg-file-resolved-pill" title="${entry.resolvedCount} resolved comment(s) in this directory">✓ ${entry.resolvedCount}</span>`);
      }
    } else if (entry.resolvedCount > 0) {
      dirBadges.push(`<span class="pg-file-comments-badge pg-file-resolved-badge pg-dir-resolved-badge all-resolved" title="${entry.resolvedCount} resolved comment(s) in this directory">✓ ${entry.resolvedCount} resolved</span>`);
    } else if (entry.commentsCount > 0) {
      dirBadges.push(`<span class="pg-file-comments-badge ${entry.hasUnres ? "has-unresolved" : "all-resolved"}" title="${entry.commentsCount} comment(s) in this directory">💬 ${entry.commentsCount}</span>`);
    }
    if (entry.robotCount > 0) {
      dirBadges.push(`<span class="pg-file-robot-badge" title="${entry.robotCount} robot comment(s) in this directory">🤖 ${entry.robotCount}</span>`);
    }
    let dirChecksHtml = "";
    if (entry.failedChecksCount > 0) {
      dirChecksHtml = `<span class="pg-file-checks-chip st-failed pg-dir-checks-chip" title="${entry.failedChecksCount} file(s) in this directory have failing TreeHugger checks"><span class="pg-checks-chip-icon">✕</span><span class="pg-checks-chip-text">${entry.failedChecksCount} fail</span></span>`;
    } else if (entry.warningChecksCount > 0) {
      dirChecksHtml = `<span class="pg-file-checks-chip st-warning pg-dir-checks-chip" title="${entry.warningChecksCount} file(s) in this directory have warning TreeHugger checks"><span class="pg-checks-chip-icon">⚠</span><span class="pg-checks-chip-text">${entry.warningChecksCount} warn</span></span>`;
    } else if (entry.runningChecksCount > 0) {
      dirChecksHtml = `<span class="pg-file-checks-chip st-running pg-dir-checks-chip" title="TreeHugger checks in progress for files in this directory"><span class="pg-checks-chip-icon pg-checks-spin">◷</span><span class="pg-checks-chip-text">running</span></span>`;
    } else if (entry.passedChecksCount > 0) {
      dirChecksHtml = `<span class="pg-file-checks-chip st-passed pg-dir-checks-chip" title="All TreeHugger checks passed for files in this directory"><span class="pg-checks-chip-icon">✓</span><span class="pg-checks-chip-text">pass</span></span>`;
    }
    const dirCommentsHtml = dirBadges.length > 0 ? `<div class="pg-file-comments-cell">${dirBadges.join("")}</div>` : "";
    const ownerBadgeHtml = entry.missingOwnerCount > 0
      ? renderPgFileOwnerBadgeHtml({ status: "missing", label: `Missing (${entry.missingOwnerCount})`, title: `${entry.missingOwnerCount} file(s) missing owner approval` }, c.number, entry.dir, true, entry.dir)
      : renderPgFileOwnerBadgeHtml({ status: "approved", label: "Approved", title: "All files in directory approved" }, c.number, entry.dir, true, entry.dir);

    return `<tr class="pg-dir-row ${isCollapsed ? "is-collapsed" : ""}" data-cl="${c.number}" data-dir="${safeDir}" tabindex="0" role="treeitem" aria-expanded="${isCollapsed ? "false" : "true"}" aria-label="Directory ${safeDir} (${entry.files.length} files)" onkeydown="onPgDirRowKeyDown(event, ${c.number}, '${safeDir}')">
      <td class="col-status">
        <button type="button" class="pg-dir-toggle-btn" onclick="togglePgDirCollapse(${c.number}, '${safeDir}')" title="${isCollapsed ? "Expand directory (Enter/Space)" : "Collapse directory (Enter/Space)"}" aria-label="Toggle directory ${safeDir}">
          <span class="pg-dir-chevron">${isCollapsed ? "▶" : "▼"}</span>
        </button>
      </td>
      <td class="col-path">
        <div class="pg-dir-path-wrap" onclick="togglePgDirCollapse(${c.number}, '${safeDir}')" title="Click to toggle directory ${safeDir}">
          <span class="pg-dir-icon" aria-hidden="true">📁</span>
          <span class="pg-dir-name">${safeDir}</span>
          <span class="pg-dir-count-pill">${entry.files.length} file${entry.files.length === 1 ? "" : "s"}</span>
          <button type="button" class="pg-file-copy-btn pg-dir-copy-btn" onclick="copyPgDirPath(event, '${safeDir}')" title="Copy directory path to clipboard (📋)"><span aria-hidden="true">📋</span></button>
          ${dirChecksHtml}
        </div>
      </td>
      <td class="col-comments">${dirCommentsHtml}</td>
      <td class="col-owners">${ownerBadgeHtml}</td>
      <td class="col-delta">
        <div class="pg-delta-cell-wrap">
          ${renderPgFileDeltaNumbersHtml(entry.additions, entry.deletions)}
          ${renderPgFileDeltaBarHtml({ additions: entry.additions, deletions: entry.deletions }, maxDelta)}
        </div>
      </td>
      <td class="col-reviewed">
        <label class="pg-dir-reviewed-label" title="Mark all ${entry.files.length} file(s) in ${safeDir} as reviewed / unreviewed" onclick="event.stopPropagation()">
          <input type="checkbox" class="pg-dir-reviewed-cb" data-cl="${c.number}" data-ps="${curPs}" data-dir="${safeDir}" ${isDirAllRev ? "checked" : ""} ${isDirSomeRev ? "data-indeterminate=\"true\"" : ""} onchange="onPgDirReviewedCheckboxChanged(this)">
          <span class="pg-dir-reviewed-text">${entry.reviewedCount}/${entry.files.length}</span>
        </label>
      </td>
    </tr>`;
  }

  function renderPgTreeFileRowHtml(c, f, isLast, curPs, revSet, isDirCollapsed, isRoot, maxDelta = null, sharedQuery = null, sharedConflictSet = null, sharedFocusedFile = null) {
    const isRev = revSet.has(f.file);
    const safeFile = escapeHtml(f.file);
    const safeDir = escapeHtml(f.dir || "");
    const safeName = escapeHtml(f.basename || f.file);
    const query = sharedQuery !== null ? sharedQuery : getPgFileQueryFilter(c.number);
    const highlightedName = highlightPgFilePathMatch(f.basename || f.file, query);
    const conflictSet = sharedConflictSet || new Set([
      ...((c && Array.isArray(c.conflicts)) ? c.conflicts : []),
      ...((state.diffsByCl && state.diffsByCl[c.number] && Array.isArray(state.diffsByCl[c.number].conflicts)) ? state.diffsByCl[c.number].conflicts : []),
    ]);
    const isConflictFile = conflictSet.size > 0 && conflictSet.has(f.file);
    const fileConflictBadgeHtml = isConflictFile
      ? `<span class="pg-file-conflict-pill" title="Merge conflict detected in ${safeFile}">⚠️ Conflict</span>`
      : "";
    const commentsCellHtml = (typeof renderPgFileCommentsBadgesHtml === "function")
      ? renderPgFileCommentsBadgesHtml(c, f.file)
      : "";
    const checksChipHtml = (typeof renderPgFileChecksChipHtml === "function")
      ? renderPgFileChecksChipHtml(c, f)
      : "";
    const status = getPgFileStatus(f);
    const statusBadge = renderPgFileStatusBadgeHtml(status, c.number);
    const ext = (typeof getPgFileExtension === "function") ? getPgFileExtension(f.file) : "";
    const safeExt = escapeHtml(ext);
    const extBadgeHtml = (typeof renderPgFileExtBadgeHtml === "function") ? renderPgFileExtBadgeHtml(ext, c.number) : "";
    const isImg = isPgImageFile(f.file) || Boolean(f.is_image);
    const isBin = Boolean(f.is_binary) || isPgBinaryFile(f.file);
    const typeIcon = isImg
      ? `<span class="pg-file-type-icon" title="Image file (${escapeHtml(getPgImageFormat(f.file))})">🖼️</span>`
      : (isBin ? `<span class="pg-file-type-icon" title="Binary file">📦</span>` : "");
    const ownerInfo = getPgFileOwnerStatus(c, f);
    const ownerBadgeHtml = renderPgFileOwnerBadgeHtml(ownerInfo, c.number, safeFile, false);
    const sizeInfo = getPgFileSizeInfo(f);
    const sizeBadgeHtml = renderPgFileSizeBadgeHtml(f, c.number);
    const deltaBarHtml = renderPgFileDeltaBarHtml(f, maxDelta);

    const focusedFile = sharedFocusedFile !== null
      ? sharedFocusedFile
      : ((state.focusedFileByCl && state.focusedFileByCl[c.number]) ||
         (state.activeFileByCl && state.activeFileByCl[c.number]) || "");
    const isFocused = Boolean(focusedFile && f.file === focusedFile);
    const oldPath = getPgFileOldPath(f);
    const sim = getPgFileSimilarity(f);
    const isCopy = status === "C" || Boolean(f && (f.is_copy || f.copied_from));
    const actionText = isCopy ? "copied from" : "renamed from";
    const simPillHtml = sim !== null
      ? ` <span class="pg-file-similarity-pill ${sim === 100 ? "is-pure" : ""}" title="Similarity score: ${sim}%">${sim}% match</span>`
      : "";
    const renamedSubHtml = oldPath
      ? `<div class="pg-file-renamed-sub" title="${isCopy ? "Copied" : "Renamed"} from ${escapeHtml(oldPath)}${sim !== null ? ` (${sim}% match)` : ""}">${actionText} ${escapeHtml(oldPath)}${simPillHtml}</div>`
      : "";

    const connector = isRoot ? "" : `<span class="pg-tree-connector" aria-hidden="true">${isLast ? "└─" : "├─"}</span>`;
    const styleAttr = (!isRoot && isDirCollapsed) ? "style=\"display: none;\"" : "";
    const safeAria = `File ${safeFile} (${status || "M"})`;

    return `<tr class="pg-file-row pg-tree-file-row ${isRev ? "is-reviewed" : ""} ${isFocused ? "is-focused" : ""}" tabindex="0" role="treeitem" aria-label="${escapeHtml(safeAria)}" data-cl="${c.number}" data-file="${safeFile}" data-dir="${safeDir}" data-status="${status}" data-extension="${safeExt}" data-size="${escapeHtml(sizeInfo.category || "")}" data-owner-status="${escapeHtml(ownerInfo.status)}" data-is-image="${isImg ? "true" : "false"}" data-is-binary="${isBin ? "true" : "false"}" ${styleAttr} onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '${safeFile}')" onkeydown="onPgFileRowKeyDown(event, ${c.number}, ${curPs}, '${safeFile}')">
      <td class="col-status">${statusBadge}</td>
      <td class="col-path">
        <div class="pg-tree-path-wrap">
          ${connector}
          <a href="javascript:void(0)" class="pg-file-row-link pg-tree-file-link" onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '${safeFile}')" title="Scroll to diff for ${safeFile}">${typeIcon}<span class="pg-tree-file-name">${highlightedName}</span></a>
          ${fileConflictBadgeHtml}
          ${extBadgeHtml}
          ${checksChipHtml}
          <button type="button" class="pg-file-copy-btn" onclick="copyPgFilePath(event, '${safeFile}')" title="Copy file path to clipboard (📋)"><span aria-hidden="true">📋</span></button>
          <button type="button" class="pg-file-edit-btn" onclick="onPgFileEditClick(event, ${c.number}, '${safeFile}', ${curPs})" title="Edit ${safeFile} in browser (e)">✏️ Edit</button>
        </div>
        ${renamedSubHtml}
      </td>
      <td class="col-comments">${commentsCellHtml}</td>
      <td class="col-owners">${ownerBadgeHtml}</td>
      <td class="col-delta">
        <div class="pg-delta-cell-wrap">
          ${sizeBadgeHtml}
          ${renderPgFileDeltaNumbersHtml(f.additions, f.deletions)}
          ${deltaBarHtml}
        </div>
      </td>
      <td class="col-reviewed">
        <label class="pg-file-reviewed-label" title="Mark ${safeFile} as reviewed (Shift+Click for range)" onclick="onPgFileReviewedLabelClick(event, this)" onmouseenter="onPgFileReviewedLabelHover(event, this, true)" onmouseleave="onPgFileReviewedLabelHover(event, this, false)">
          <input type="checkbox" class="pg-file-reviewed-cb" data-cl="${c.number}" data-ps="${curPs}" data-file="${safeFile}" data-dir="${safeDir}" ${isRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this, event)" onclick="onPgFileReviewedCheckboxClick(event, this)">
          <span>Reviewed</span>
        </label>
      </td>
    </tr>`;
  }

  function renderPgTreeRowsHtml(c, fileList, revSet, curPs, maxDelta = null) {
    const dirGroups = groupPgFilesByDirectory(fileList, revSet, c);
    const collapsedSet = getPgCollapsedDirs(c.number);
    const sharedQuery = getPgFileQueryFilter(c.number);
    const sharedConflictSet = new Set([
      ...((c && Array.isArray(c.conflicts)) ? c.conflicts : []),
      ...((state.diffsByCl && state.diffsByCl[c.number] && Array.isArray(state.diffsByCl[c.number].conflicts)) ? state.diffsByCl[c.number].conflicts : []),
    ]);
    const sharedFocusedFile = (state.focusedFileByCl && state.focusedFileByCl[c.number]) ||
                              (state.activeFileByCl && state.activeFileByCl[c.number]) || "";
    let html = "";
    for (let gIdx = 0; gIdx < dirGroups.length; gIdx++) {
      const entry = dirGroups[gIdx];
      const isRoot = !entry.dir;
      const isDirCollapsed = collapsedSet.has(entry.dir);
      if (!isRoot) {
        html += renderPgDirRowHtml(c, entry, curPs, isDirCollapsed, revSet, maxDelta);
      }
      const files = entry.files;
      for (let idx = 0; idx < files.length; idx++) {
        const isLast = idx === files.length - 1;
        html += renderPgTreeFileRowHtml(c, files[idx], isLast, curPs, revSet, isDirCollapsed, isRoot, maxDelta, sharedQuery, sharedConflictSet, sharedFocusedFile);
      }
    }
    return html;
  }

  const _pgFileListHtmlCache = new Map();

  function renderPgFileListHtml(c, bundle) {
    const rawFileList = (bundle && Array.isArray(bundle.files)) ? bundle.files : [];
    if (rawFileList.length === 0) return "";
    const isTree = getPgFileTreeMode(c.number);
    const flCtx = bundle && bundle._renderPassFlCtx;
    let curPs;
    let revSet;
    let isHideReviewed;
    let isFilterMissingActive;
    let isFilterCommentsActive;
    let commentsMode;
    let activeStatus;
    let activeExt;
    let activeSize;
    let query;
    let commentIdx;
    let collapsedSet;
    if (flCtx) {
      curPs = flCtx.curPs;
      revSet = flCtx.revSet;
      collapsedSet = flCtx.collapsedSet;
      commentIdx = flCtx.commentIdx;
      query = flCtx.query;
      isFilterMissingActive = flCtx.isFilterMissingActive;
      activeStatus = flCtx.activeStatus;
      isHideReviewed = flCtx.isHideReviewed;
      isFilterCommentsActive = flCtx.isFilterCommentsActive;
      commentsMode = isFilterCommentsActive
        ? ((typeof getPgCommentsFilterMode === "function") ? getPgCommentsFilterMode(c.number) : "unresolved")
        : "";
      activeExt = flCtx.activeExt;
      activeSize = flCtx.activeSize;
    } else {
      const selPsMeta = (state.selectedPatchsetByCl && state.selectedPatchsetByCl[c.number]) || {};
      curPs = Number(selPsMeta.ps || (bundle && bundle.patch_set) || c.patch_set || 1);
      revSet = getPgReviewedSet(c.number, curPs);
      isHideReviewed = (typeof isPgHideReviewedActive === "function") && isPgHideReviewedActive(c.number);
      isFilterMissingActive = (typeof isPgOwnerFilterActive === "function") && isPgOwnerFilterActive(c.number);
      isFilterCommentsActive = (typeof isPgCommentsFilterActive === "function") && isPgCommentsFilterActive(c.number);
      commentsMode = (typeof getPgCommentsFilterMode === "function") ? getPgCommentsFilterMode(c.number) : (isFilterCommentsActive ? "unresolved" : "");
      activeStatus = (typeof getPgFileStatusFilter === "function") ? getPgFileStatusFilter(c.number) : "";
      activeExt = (typeof getPgFileExtensionFilter === "function") ? getPgFileExtensionFilter(c.number) : "";
      activeSize = (typeof getPgFileSizeFilter === "function") ? getPgFileSizeFilter(c.number) : "";
      query = getPgFileQueryFilter(c.number);
      commentIdx = (typeof getInlineCommentsIndexForCl === "function")
        ? getInlineCommentsIndexForCl(c.number, bundle)
        : null;
      collapsedSet = getPgCollapsedSet(c.number, curPs);
    }
    const commentSig = commentIdx ? commentIdx.sig : "";
    const statsByFile = commentSig ? new Map() : null;
    const getStatsForFile = (filePath) => {
      if (!commentSig) return _PG_EMPTY_FILE_COMMENT_STATS;
      if (statsByFile.has(filePath)) return statsByFile.get(filePath);
      const st = (typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(c.number, filePath) : _PG_EMPTY_FILE_COMMENT_STATS;
      statsByFile.set(filePath, st);
      return st;
    };

    const presubmitEntry = state.presubmitTargetsByCl && state.presubmitTargetsByCl[c.number];
    let hasAnyChecks = Boolean(
      (c && c.checks) ||
      (bundle && bundle.checks) ||
      (state.checksRunsByCl && state.checksRunsByCl[c.number]) ||
      (presubmitEntry && presubmitEntry.data && Array.isArray(presubmitEntry.data.targets) && presubmitEntry.data.targets.length > 0)
    );
    const hasConflicts = Boolean((c && Array.isArray(c.conflicts) && c.conflicts.length > 0) || (bundle && Array.isArray(bundle.conflicts) && bundle.conflicts.length > 0));
    let canMemoFileList =
      !query &&
      !hasAnyChecks &&
      !hasConflicts &&
      !state.fileListRangeSelection &&
      (!state.copyFilePathsMenu || !state.copyFilePathsMenu.isOpen);

    const clOwnerPrefix = computePgClOwnerStatusPrefix(c);
    let filesSig = "";
    if (canMemoFileList) {
      let fh = (FNV1A_OFFSET_BASIS ^ rawFileList.length) | 0;
      for (let i = 0; i < rawFileList.length; i++) {
        const f = rawFileList[i];
        if (f.checks || f.check_runs) {
          hasAnyChecks = true;
          canMemoFileList = false;
          break;
        }
        const ownersLen = Array.isArray(f.owners) ? f.owners.length : 0;
        const flSub = f._flSub || (f._flSub = {});
        if (
          flSub.hash === undefined ||
          flSub.file !== f.file ||
          flSub.status !== f.status ||
          flSub.add !== f.additions ||
          flSub.del !== f.deletions ||
          flSub.ownerStatus !== f.owner_status ||
          flSub.ownerApproved !== f.owner_approved ||
          flSub.ownerDetail !== f.owner_detail ||
          flSub.ownerLabel !== f.owner_label ||
          flSub.ownersLen !== ownersLen ||
          flSub.oldPath !== f.old_path ||
          flSub.sim !== f.similarity
        ) {
          flSub.file = f.file;
          flSub.status = f.status;
          flSub.add = f.additions;
          flSub.del = f.deletions;
          flSub.ownerStatus = f.owner_status;
          flSub.ownerApproved = f.owner_approved;
          flSub.ownerDetail = f.owner_detail;
          flSub.ownerLabel = f.owner_label;
          flSub.ownersLen = ownersLen;
          flSub.oldPath = f.old_path;
          flSub.sim = f.similarity;
          let ih = fnv1aHashStr(FNV1A_OFFSET_BASIS, f.file);
          if (f.status) ih = fnv1aHashStr(ih, f.status);
          ih = Math.imul(ih ^ (f.additions | 0), FNV1A_PRIME);
          ih = Math.imul(ih ^ (f.deletions | 0), FNV1A_PRIME);
          if (f.owner_status) ih = fnv1aHashStr(ih, f.owner_status);
          if (f.owner_approved !== undefined && f.owner_approved !== null) ih = fnv1aHashStr(ih, String(f.owner_approved));
          if (f.owner_detail) ih = fnv1aHashStr(ih, f.owner_detail);
          if (f.owner_label) ih = fnv1aHashStr(ih, f.owner_label);
          if (ownersLen) ih = Math.imul(ih ^ ownersLen, FNV1A_PRIME);
          if (f.old_path) ih = fnv1aHashStr(ih, f.old_path);
          if (f.similarity !== undefined && f.similarity !== null) ih = fnv1aHashStr(ih, String(f.similarity));
          flSub.hash = ih;
        }
        fh = Math.imul(fh ^ flSub.hash, FNV1A_PRIME) | 0;
      }
      if (canMemoFileList) {
        filesSig = String(fh >>> 0);
      }
    }

    const commitMsgBadgeHtml = ((commentSig || hasAnyChecks) && typeof renderPgFileCommentsBadgesHtml === "function")
      ? renderPgFileCommentsBadgesHtml(c, "/COMMIT_MSG")
      : "";

    const collapsedDirsSet = isTree ? getPgCollapsedDirs(c.number) : null;
    const fileSort = getPgFileSort(c.number);
    let commitMsgLines = 0;
    if (
      bundle &&
      bundle._cachedCmLinesVal !== undefined &&
      bundle._cachedCmMsgRef === (c && c.commit_message) &&
      bundle._cachedCmBundleMsgRef === bundle.commit_message &&
      bundle._cachedCmSubjRef === (c && c.subject)
    ) {
      commitMsgLines = bundle._cachedCmLinesVal;
    } else {
      const fullCommitMsg = (typeof getFullCommitMessage === "function") ? getFullCommitMessage(c, bundle) : "";
      commitMsgLines = (typeof getCommitMessageTotalLines === "function") ? getCommitMessageTotalLines(fullCommitMsg) : 0;
      if (bundle) {
        bundle._cachedCmMsgRef = c && c.commit_message;
        bundle._cachedCmBundleMsgRef = bundle.commit_message;
        bundle._cachedCmSubjRef = c && c.subject;
        bundle._cachedCmLinesVal = commitMsgLines;
      }
    }
    const isMergeCommit = (typeof isPgMergeCommit === "function") && isPgMergeCommit(c, bundle);
    const mergeCommits = isMergeCommit && (typeof getPgMergeList === "function") ? getPgMergeList(c, bundle) : [];
    const colSetCount = collapsedSet.size;
    const isCommitMsgCollapsed = colSetCount > 0 && collapsedSet.has("/COMMIT_MSG");
    const isMergeListCollapsed = colSetCount > 0 && collapsedSet.has("/MERGE_LIST");
    const explicitFocusedFile = (state.focusedFileByCl && state.focusedFileByCl[c.number]) ||
                                (state.activeFileByCl && state.activeFileByCl[c.number]) || "";
    const effectiveFocusedFile = explicitFocusedFile || (rawFileList[0] && rawFileList[0].file) || "";

    const collapsedDirsCount = (collapsedDirsSet && collapsedDirsSet.size) ? collapsedDirsSet.size : 0;
    const revSetCount = revSet.size;
    const mergeKeyNum = isMergeCommit ? mergeCommits.length + 1 : 0;
    const flFlags =
      (isTree ? 1 : 0) |
      (isHideReviewed ? 2 : 0) |
      (isFilterMissingActive ? 4 : 0) |
      (isFilterCommentsActive ? 8 : 0) |
      (isCommitMsgCollapsed ? 16 : 0) |
      (isMergeListCollapsed ? 32 : 0);
    if (
      canMemoFileList &&
      bundle &&
      _pgFileListHtmlCache.size > 0 &&
      collapsedDirsCount === 0 &&
      revSetCount === 0 &&
      colSetCount === 0
    ) {
      const flSlot = isTree ? bundle._flSubTree : bundle._flSubFlat;
      if (
        flSlot &&
        typeof flSlot.html === "string" &&
        flSlot.clNum === c.number &&
        flSlot.curPs === curPs &&
        flSlot.len === rawFileList.length &&
        flSlot.flags === flFlags &&
        flSlot.commentsMode === commentsMode &&
        flSlot.activeStatus === activeStatus &&
        flSlot.activeExt === activeExt &&
        flSlot.activeSize === activeSize &&
        flSlot.sortCol === fileSort.col &&
        flSlot.sortDir === fileSort.dir &&
        flSlot.focusedFile === effectiveFocusedFile &&
        flSlot.cmLines === commitMsgLines &&
        flSlot.cmBadge === commitMsgBadgeHtml &&
        flSlot.mergeKey === mergeKeyNum &&
        flSlot.ownerPrefix === clOwnerPrefix &&
        flSlot.commentSig === commentSig &&
        flSlot.filesSig === filesSig
      ) {
        bundle._cachedFileListKey = flSlot.key;
        bundle._cachedFileListHtml = flSlot.html;
        return flSlot.html;
      }
    }

    const fileListCacheKey = canMemoFileList
      ? `${c.number}|${curPs}|${rawFileList.length}|${isTree ? 1 : 0}|${collapsedDirsCount ? Array.from(collapsedDirsSet).join(",") : ""}|${isHideReviewed ? 1 : 0}|${isFilterMissingActive ? 1 : 0}|${isFilterCommentsActive ? 1 : 0}|${commentsMode}|${activeStatus}|${activeExt}|${activeSize}|${fileSort.col}|${fileSort.dir}|${effectiveFocusedFile}|${commitMsgLines}|${commitMsgBadgeHtml}|${mergeKeyNum}|${isCommitMsgCollapsed ? 1 : 0}|${isMergeListCollapsed ? 1 : 0}|${revSetCount ? Array.from(revSet).join(",") : ""}|${colSetCount ? Array.from(collapsedSet).join(",") : ""}|${clOwnerPrefix}|${commentSig}|${filesSig}`
      : "";
    const recordFlSlot = (htmlVal) => {
      if (!bundle || !fileListCacheKey) return;
      bundle._cachedFileListKey = fileListCacheKey;
      bundle._cachedFileListHtml = htmlVal;
      if (collapsedDirsCount === 0 && revSetCount === 0 && colSetCount === 0) {
        const wSlot = isTree
          ? (bundle._flSubTree || (bundle._flSubTree = {}))
          : (bundle._flSubFlat || (bundle._flSubFlat = {}));
        wSlot.clNum = c.number;
        wSlot.curPs = curPs;
        wSlot.len = rawFileList.length;
        wSlot.flags = flFlags;
        wSlot.commentsMode = commentsMode;
        wSlot.activeStatus = activeStatus;
        wSlot.activeExt = activeExt;
        wSlot.activeSize = activeSize;
        wSlot.sortCol = fileSort.col;
        wSlot.sortDir = fileSort.dir;
        wSlot.focusedFile = effectiveFocusedFile;
        wSlot.cmLines = commitMsgLines;
        wSlot.cmBadge = commitMsgBadgeHtml;
        wSlot.mergeKey = mergeKeyNum;
        wSlot.ownerPrefix = clOwnerPrefix;
        wSlot.commentSig = commentSig;
        wSlot.filesSig = filesSig;
        wSlot.key = fileListCacheKey;
        wSlot.html = htmlVal;
      }
    };
    if (fileListCacheKey) {
      if (bundle && bundle._cachedFileListKey === fileListCacheKey && bundle._cachedFileListHtml !== undefined && _pgFileListHtmlCache.size > 0) {
        return bundle._cachedFileListHtml;
      }
      const cachedListHtml = _pgFileListHtmlCache.get(fileListCacheKey);
      if (cachedListHtml !== undefined) {
        recordFlSlot(cachedListHtml);
        return cachedListHtml;
      }
    }

    // Apply active column sorting
    const sortedFileList = (flCtx && Array.isArray(flCtx.sortedFiles))
      ? flCtx.sortedFiles
      : getPgSortedFileList(c, rawFileList);
    let fileList = sortedFileList;
    if (activeStatus) {
      fileList = fileList.filter(f => getPgFileStatus(f) === activeStatus);
    }
    if (activeExt) {
      fileList = fileList.filter(f => (typeof getPgFileExtension === "function") && getPgFileExtension(f.file) === activeExt);
    }
    if (activeSize) {
      fileList = fileList.filter(f => (typeof getPgFileSizeInfo === "function") && getPgFileSizeInfo(f).category === activeSize);
    }
    if (isHideReviewed) {
      fileList = fileList.filter(f => !revSet.has(f.file));
    }
    if (isFilterMissingActive) {
      fileList = fileList.filter(f => getPgFileOwnerStatus(c, f).status === "missing");
    }
    if (isFilterCommentsActive) {
      fileList = fileList.filter(f => {
        const stats = getStatsForFile(f.file);
        if (commentsMode === "drafts") {
          return Boolean(stats.hasDrafts);
        } else if (commentsMode === "comments") {
          return Boolean(stats.hasComments || stats.total > 0 || stats.hasDrafts);
        }
        return Boolean(stats.hasUnresolved || stats.hasDrafts);
      });
    }
    if (query) {
      fileList = fileList.filter(f => {
        const stats = getStatsForFile(f.file);
        const ownerInfo = getPgFileOwnerStatus(c, f);
        const fChecks = (typeof getPgFileChecksStatus === "function") ? getPgFileChecksStatus(c, f) : {};
        return matchesPgFileFilter(f.file, query, {
          status: getPgFileStatus(f),
          sizeCategory: (typeof getPgFileSizeInfo === "function") ? getPgFileSizeInfo(f).category : "",
          oldPath: getPgFileOldPath(f),
          isReviewed: revSet.has(f.file),
          hasComments: Boolean(stats.hasComments || stats.total > 0 || stats.commentCount > 0),
          hasDrafts: Boolean(stats.hasDrafts),
          hasUnresolved: Boolean(stats.hasUnresolved),
          hasResolved: Boolean(stats.hasResolved),
          hasRobot: Boolean(stats.hasRobot),
          isOwnerMissing: ownerInfo.status === "missing",
          isImage: isPgImageFile(f.file) || Boolean(f.is_image),
          isBinary: Boolean(f.is_binary) || isPgBinaryFile(f.file),
          hasChecks: Boolean(fChecks.hasChecks),
          checksStatus: fChecks.status,
          hasFailedChecks: Boolean(fChecks.failedCount > 0),
          hasWarningChecks: Boolean(fChecks.warningCount > 0),
          hasRunningChecks: Boolean(fChecks.runningCount > 0),
          hasPassedChecks: Boolean(fChecks.passedCount > 0),
        });
      });
    }

    let reviewedCount = 0;
    let unresolvedCount = 0;
    let commentedFilesCount = 0;
    let draftsFilesCount = 0;
    let missingOwnerCount = 0;
    let totalAdditions = 0;
    let totalDeletions = 0;
    let totalCommentsCount = 0;
    let totalUnresolvedCount = 0;
    let maxDelta = 0;
    const statusCounts = { M: 0, A: 0, D: 0, R: 0, C: 0 };
    const extCounts = {};
    const sizeCounts = { XS: 0, S: 0, M: 0, L: 0, XL: 0 };

    for (let i = 0; i < rawFileList.length; i++) {
      const f = rawFileList[i];
      if (revSet.has(f.file)) reviewedCount++;
      const st = getStatsForFile(f.file);
      if (st.hasUnresolved || st.hasDrafts) unresolvedCount++;
      if (st.hasComments || st.total > 0 || st.hasDrafts) commentedFilesCount++;
      if (st.hasDrafts || st.draftCount > 0) draftsFilesCount++;
      const ownerInfo = (typeof getPgFileOwnerStatus === "function") ? getPgFileOwnerStatus(c, f, clOwnerPrefix) : _NA_FILE_OWNER_STATUS;
      f._loopOwnerInfo = ownerInfo;
      f._loopOwnerClPrefix = clOwnerPrefix;
      f._loopOwnerRawSt = f.owner_status;
      f._loopOwnerRawApp = f.owner_approved;
      if (ownerInfo.status === "missing") {
        missingOwnerCount++;
      }
      const add = Math.max(0, Number(f.additions || 0));
      const del = Math.max(0, Number(f.deletions || 0));
      totalAdditions += add;
      totalDeletions += del;
      if (add + del > maxDelta) maxDelta = add + del;
      totalCommentsCount += Number(st.commentCount || st.total || 0);
      totalUnresolvedCount += Number(st.unresolvedCount || (st.hasUnresolved ? 1 : 0));

      const fSt = (f._cachedFilePath === f.file && f._cachedMetaStatusRaw === f.status && f._cachedStatus)
        ? f._cachedStatus
        : getPgFileStatus(f);
      f._loopStatus = fSt;
      f._loopStatusFile = f.file;
      if (statusCounts[fSt] !== undefined) statusCounts[fSt]++;
      else statusCounts.M++;

      const ext = (f._cachedFilePath === f.file && f._cachedExt !== undefined)
        ? f._cachedExt
        : ((typeof getPgFileExtension === "function") ? getPgFileExtension(f.file) : "");
      f._loopExt = ext;
      if (ext) extCounts[ext] = (extCounts[ext] || 0) + 1;

      const szInfo = (typeof getPgFileSizeInfo === "function") ? getPgFileSizeInfo(f) : null;
      f._loopSizeInfo = szInfo;
      if (szInfo && szInfo.category && sizeCounts[szInfo.category] !== undefined) {
        sizeCounts[szInfo.category]++;
      }
      if (!hasAnyChecks && (f.checks || f.check_runs)) hasAnyChecks = true;
    }
    const unreviewedCount = Math.max(0, rawFileList.length - reviewedCount);
    const focusedFile = explicitFocusedFile || (fileList[0] && fileList[0].file) || "";
    const distinctExts = Object.keys(extCounts).sort((a, b) => extCounts[b] - extCounts[a] || a.localeCompare(b));
    const extPresetsHtml = (distinctExts.length >= 2) ? distinctExts.slice(0, 5).map(ext => {
      const safeExt = escapeHtml(ext);
      const extId = ext.replace(/[^a-zA-Z0-9_-]/g, "_");
      const isAct = (activeExt === ext);
      return `<button type="button" class="pg-filter-preset-btn pg-ext-preset-btn ${isAct ? "is-active" : ""}" id="btnFilterPresetExt-${c.number}-${extId}" data-cl="${c.number}" data-ext="${safeExt}" onclick="togglePgFileExtensionFilter(${c.number}, '${safeExt}')" title="Filter files: show only ${safeExt} files · Alt+E to cycle">${safeExt} (${extCounts[ext]})</button>`;
    }).join("") : "";
    const sizePresetsHtml = ["XS", "S", "M", "L", "XL"].map(cat => {
      if (!sizeCounts[cat]) return "";
      const lower = cat.toLowerCase();
      const isAct = (activeSize === cat);
      const title = `Filter files: show only ${cat} (${cat === "XS" ? "1-9 lines" : (cat === "S" ? "10-49 lines" : (cat === "M" ? "50-249 lines" : (cat === "L" ? "250-999 lines" : "1000+ lines")))}) · Alt+B to cycle`;
      return `<button type="button" class="pg-filter-preset-btn pg-size-preset-btn size-${lower} ${isAct ? "is-active" : ""}" id="btnFilterPresetSize${cat}-${c.number}" data-cl="${c.number}" data-size="${cat}" onclick="togglePgFileSizeFilter(${c.number}, '${cat}')" title="${escapeHtml(title)}">${cat} (${sizeCounts[cat]})</button>`;
    }).join("");

    const unreviewedBtnLabel = unreviewedCount === 0
      ? "👁️ All reviewed ✓"
      : `👁️ Unreviewed (${unreviewedCount})`;

    const collapsedFilesCount = fileList.filter(f => collapsedSet.has(f.file)).length;
    const expandedFilesCount = fileList.length - collapsedFilesCount;
    const allFilesCollapsed = fileList.length > 0 && collapsedFilesCount === fileList.length;
    const allFilesExpanded = fileList.length > 0 && expandedFilesCount === fileList.length;

    const hiddenReviewedCount = isHideReviewed ? reviewedCount : 0;
    const hasAnyFilter = Boolean(isFilterMissingActive || isFilterCommentsActive || query || (isHideReviewed && hiddenReviewedCount > 0) || activeStatus || activeExt || activeSize);
    let filterBarText = "";
    if (isFilterCommentsActive && isFilterMissingActive && query) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (with unresolved comments/drafts, missing owner approval and matching "<strong>${escapeHtml(query)}</strong>")`;
    } else if (isFilterCommentsActive && query) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (with unresolved comments/drafts and matching "<strong>${escapeHtml(query)}</strong>")`;
    } else if (isFilterCommentsActive && isFilterMissingActive) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (with unresolved comments/drafts and missing code owner approval)`;
    } else if (isFilterCommentsActive) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files with unresolved comments or drafts`;
    } else if (isFilterMissingActive && query) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (missing owner approval and matching "<strong>${escapeHtml(query)}</strong>")`;
    } else if (isFilterMissingActive) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files missing code owner approval`;
    } else if (query) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files matching "<strong>${escapeHtml(query)}</strong>"`;
    } else if (isHideReviewed && hiddenReviewedCount > 0) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (<strong>${hiddenReviewedCount}</strong> reviewed hidden)`;
    }

    if (filterBarText && isHideReviewed && hiddenReviewedCount > 0 && (query || isFilterMissingActive || isFilterCommentsActive)) {
      filterBarText += ` · <em>${hiddenReviewedCount} reviewed hidden</em>`;
    }
    if (filterBarText && activeStatus) {
      const statusNames = { A: "added files", M: "modified files", D: "deleted files", R: "renamed files" };
      filterBarText += ` · <em>${statusNames[activeStatus] || activeStatus}</em>`;
    } else if (!filterBarText && activeStatus) {
      const statusNames = { A: "Added (+)", M: "Modified (M)", D: "Deleted (-)", R: "Renamed (→)" };
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (${statusNames[activeStatus] || activeStatus})`;
    }
    if (filterBarText && activeExt) {
      filterBarText += ` · <em>${escapeHtml(activeExt)}</em>`;
    } else if (!filterBarText && activeExt) {
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (${escapeHtml(activeExt)})`;
    }
    if (filterBarText && activeSize) {
      const sizeNames = { XS: "extra small (XS)", S: "small (S)", M: "medium (M)", L: "large (L)", XL: "extra large (XL)" };
      filterBarText += ` · <em>${sizeNames[activeSize] || activeSize} files</em>`;
    } else if (!filterBarText && activeSize) {
      const sizeNames = { XS: "Extra small (XS)", S: "Small (S)", M: "Medium (M)", L: "Large (L)", XL: "Extra large (XL)" };
      filterBarText = `Showing <strong>${fileList.length}</strong> of <strong>${rawFileList.length}</strong> files (${sizeNames[activeSize] || activeSize})`;
    }

    const filterNoticeHtml = `<div class="pg-file-list-filter-bar" style="${hasAnyFilter ? "" : "display:none;"}">
        <span class="pg-filter-bar-text">${filterBarText}</span>
        <button type="button" class="pg-btn-subtle pg-btn-clear-filter" onclick="clearAllPgFileFilters(${c.number})" title="Clear all active filters and show all files">Clear filter</button>
      </div>`;

    const mergeListStats = (isMergeCommit && typeof getPgFileCommentStats === "function") ? getPgFileCommentStats(c.number, "/MERGE_LIST") : {};
    const isMergeListRev = isMergeCommit && revSet.has("/MERGE_LIST");
    const isMergeListFocused = Boolean(isMergeCommit && focusedFile && focusedFile === "/MERGE_LIST");
    const mergeListBadgeHtml = (isMergeCommit && typeof renderPgFileCommentsBadgesHtml === "function")
      ? renderPgFileCommentsBadgesHtml(c, "/MERGE_LIST")
      : "";
    const showMergeList = isMergeCommit && !isFilterMissingActive &&
      (!isFilterCommentsActive || mergeListStats.hasUnresolved || mergeListStats.hasDrafts) &&
      (!query || matchesPgFileFilter("/MERGE_LIST", query, {
        status: "M",
        isReviewed: isMergeListRev,
        hasComments: Boolean(mergeListStats.commentCount > 0),
        hasDrafts: Boolean(mergeListStats.hasDrafts),
        hasUnresolved: Boolean(mergeListStats.hasUnresolved),
        isOwnerMissing: false,
      })) &&
      (!isHideReviewed || !isMergeListRev) &&
      (!activeStatus || activeStatus === "M") &&
      (!activeExt) &&
      (!activeSize);

    const commitMsgStats = ((commentSig || isFilterCommentsActive || query) && typeof getPgFileCommentStats === "function")
      ? getPgFileCommentStats(c.number, "/COMMIT_MSG")
      : {};
    const isCommitMsgRev = revSet.has("/COMMIT_MSG");
    const isCommitMsgFocused = Boolean(focusedFile && focusedFile === "/COMMIT_MSG");
    const showCommitMsg = !isFilterMissingActive &&
      (!isFilterCommentsActive || commitMsgStats.hasUnresolved || commitMsgStats.hasDrafts) &&
      (!query || matchesPgFileFilter("/COMMIT_MSG", query, {
        status: "M",
        isReviewed: isCommitMsgRev,
        hasComments: Boolean(commitMsgStats.commentCount > 0),
        hasDrafts: Boolean(commitMsgStats.hasDrafts),
        hasUnresolved: Boolean(commitMsgStats.hasUnresolved),
        isOwnerMissing: false,
      })) &&
      (!isHideReviewed || !isCommitMsgRev) &&
      (!activeStatus || activeStatus === "M") &&
      (!activeExt) &&
      (!activeSize);

    const isDefaultSort = fileSort.col === "path" && fileSort.dir === "asc";
    const sortVal = `${fileSort.col}:${fileSort.dir}`;

    const visibleCount = fileList.length;
    const visibleReviewedCount = (fileList === rawFileList) ? reviewedCount : fileList.filter(f => revSet.has(f.file)).length;
    const isAllRev = visibleCount > 0 && visibleReviewedCount === visibleCount;
    const someRev = visibleReviewedCount > 0 && !isAllRev;
    const markAllBtnLabel = isAllRev
      ? (hasAnyFilter ? `✕ Unmark filtered (${visibleCount})` : "✕ Mark all unreviewed")
      : (hasAnyFilter ? `✓ Mark filtered reviewed (${visibleCount})` : "✓ Mark all reviewed");

    const renderSortIndicator = (col) => {
      const isColActive = (col === "delta" && (fileSort.col === "delta" || fileSort.col === "size")) || (fileSort.col === col);
      if (isColActive) {
        return `<span class="pg-sort-arrow dir-${fileSort.dir}">${fileSort.dir === "asc" ? "▲" : "▼"}</span>`;
      }
      return `<span class="pg-sort-arrow is-idle" aria-hidden="true">⇅</span>`;
    };

    let fileRowsHtml = "";
    const cConflicts = (c && Array.isArray(c.conflicts) && c.conflicts.length > 0) ? c.conflicts : null;
    const bConflicts = (bundle && Array.isArray(bundle.conflicts) && bundle.conflicts.length > 0) ? bundle.conflicts : null;
    const conflictSet = (cConflicts || bConflicts)
      ? new Set([...(cConflicts || []), ...(bConflicts || [])])
      : null;
    if (isTree) {
      fileRowsHtml = renderPgTreeRowsHtml(c, fileList, revSet, curPs, maxDelta);
    } else {
      let rowsAcc = "";
      const extBadgeMap = Object.create(null);
      const hasExtBadgeFn = typeof renderPgFileExtBadgeHtml === "function";
      for (let i = 0; i < fileList.length; i++) {
        const f = fileList[i];
        const isRev = revSet.has(f.file);
        const hasCachedPath = f._cachedFilePath === f.file;
        let safeFile;
        if (hasCachedPath && typeof f._cachedSafeFile === "string") {
          safeFile = f._cachedSafeFile;
        } else {
          safeFile = escapeHtml(f.file);
          f._cachedFilePath = f.file;
          f._cachedSafeFile = safeFile;
        }
        const highlightedFile = query ? highlightPgFilePathMatch(f.file, query) : safeFile;
        const isConflictFile = conflictSet !== null && conflictSet.has(f.file);
        const fileConflictBadgeHtml = isConflictFile
          ? `<span class="pg-file-conflict-pill" title="Merge conflict detected in ${safeFile}">⚠️ Conflict</span>`
          : "";
        const isFocused = Boolean(focusedFile && f.file === focusedFile);
        const commentsCellHtml = (commentSig && typeof renderPgFileCommentsBadgesHtml === "function")
          ? renderPgFileCommentsBadgesHtml(c, f.file, getStatsForFile(f.file))
          : "";
        const checksChipHtml = (hasAnyChecks && typeof renderPgFileChecksChipHtml === "function")
          ? renderPgFileChecksChipHtml(c, f)
          : "";
        const status = f._loopStatus || getPgFileStatus(f);
        const statusBadge = renderPgFileStatusBadgeHtml(status, c.number);
        const ext = f._loopExt !== undefined ? f._loopExt : ((typeof getPgFileExtension === "function") ? getPgFileExtension(f.file) : "");
        const safeExt = (hasCachedPath && f._cachedSafeExt !== undefined && f._cachedExt === ext)
          ? f._cachedSafeExt
          : (f._cachedExt = ext, f._cachedSafeExt = escapeHtml(ext));
        let extBadgeHtml = "";
        if (ext && hasExtBadgeFn) {
          extBadgeHtml = extBadgeMap[ext];
          if (extBadgeHtml === undefined) {
            extBadgeHtml = renderPgFileExtBadgeHtml(ext, c.number);
            extBadgeMap[ext] = extBadgeHtml;
          }
        }
        let isImg;
        if (hasCachedPath && f._cachedIsImg !== undefined) {
          isImg = Boolean(f.is_image) || f._cachedIsImg;
        } else {
          const rawImg = isPgImageFile(f.file);
          f._cachedIsImg = rawImg;
          isImg = Boolean(f.is_image) || rawImg;
        }
        let isBin;
        if (hasCachedPath && f._cachedIsBin !== undefined) {
          isBin = Boolean(f.is_binary) || isImg || f._cachedIsBin;
        } else {
          const rawBin = isImg || isPgBinaryFile(f.file);
          f._cachedIsBin = rawBin;
          isBin = Boolean(f.is_binary) || rawBin;
        }
        const typeIcon = isImg
          ? `<span class="pg-file-type-icon" title="Image file (${escapeHtml(getPgImageFormat(f.file))})">🖼️</span>`
          : (isBin ? `<span class="pg-file-type-icon" title="Binary file">📦</span>` : "");
        const ownerInfo = f._loopOwnerInfo || getPgFileOwnerStatus(c, f, clOwnerPrefix);
        const ownerBadgeHtml = renderPgFileOwnerBadgeHtml(ownerInfo, c.number, safeFile, false, "", true);
        f._cachedOwnerBadgeCl = Number(c.number);
        f._cachedOwnerBadgeInfo = ownerInfo;
        f._cachedOwnerBadgeTarget = safeFile;
        f._cachedOwnerBadgeHtml = ownerBadgeHtml;
        const sizeInfo = f._loopSizeInfo || getPgFileSizeInfo(f);
        const sizeBadgeHtml = renderPgFileSizeBadgeHtml(f, c.number, sizeInfo);
        const deltaBarHtml = renderPgFileDeltaBarHtml(f, maxDelta);
        const oldPath = (hasCachedPath && f._cachedOldPath !== undefined) ? f._cachedOldPath : getPgFileOldPath(f);
        const sim = (hasCachedPath && f._cachedSim !== undefined) ? f._cachedSim : getPgFileSimilarity(f);
        const isCopy = status === "C" || Boolean(f && (f.is_copy || f.copied_from));
        const actionText = isCopy ? "copied from" : "renamed from";
        const simPillHtml = sim !== null
          ? ` <span class="pg-file-similarity-pill ${sim === 100 ? "is-pure" : ""}" title="Similarity score: ${sim}%">${sim}% match</span>`
          : "";
        const safeOldPath = oldPath ? escapeHtml(oldPath) : "";
        const renamedSubHtml = oldPath
          ? `<div class="pg-file-renamed-sub" title="${isCopy ? "Copied" : "Renamed"} from ${safeOldPath}${sim !== null ? ` (${sim}% match)` : ""}">${actionText} ${safeOldPath}${simPillHtml}</div>`
          : "";
        const safeAria = `File ${safeFile} (${status || "M"})`;
        rowsAcc += `<tr class="pg-file-row ${isRev ? "is-reviewed" : ""} ${isFocused ? "is-focused" : ""}" tabindex="0" role="row" aria-label="${safeAria}" data-cl="${c.number}" data-file="${safeFile}" data-status="${status}" data-extension="${safeExt}" data-size="${escapeHtml(sizeInfo.category || "")}" data-owner-status="${escapeHtml(ownerInfo.status)}" data-is-image="${isImg ? "true" : "false"}" data-is-binary="${isBin ? "true" : "false"}" onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '${safeFile}')" onkeydown="onPgFileRowKeyDown(event, ${c.number}, ${curPs}, '${safeFile}')">
          <td class="col-status">${statusBadge}</td>
          <td class="col-path">
            <div class="pg-file-path-wrap">
              <a href="javascript:void(0)" class="pg-file-row-link" onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '${safeFile}')" title="Scroll to diff for ${safeFile}">${typeIcon}${highlightedFile}</a>
              ${fileConflictBadgeHtml}
              ${extBadgeHtml}
              ${checksChipHtml}
              <button type="button" class="pg-file-copy-btn" onclick="copyPgFilePath(event, '${safeFile}')" title="Copy file path to clipboard (📋)"><span aria-hidden="true">📋</span></button>
              <button type="button" class="pg-file-edit-btn" onclick="onPgFileEditClick(event, ${c.number}, '${safeFile}', ${curPs})" title="Edit ${safeFile} in browser (e)">✏️ Edit</button>
            </div>
            ${renamedSubHtml}
          </td>
          <td class="col-comments">${commentsCellHtml}</td>
          <td class="col-owners">${ownerBadgeHtml}</td>
          <td class="col-delta">
            <div class="pg-delta-cell-wrap">
              ${sizeBadgeHtml}
              ${renderPgFileDeltaNumbersHtml(f.additions, f.deletions)}
              ${deltaBarHtml}
            </div>
          </td>
          <td class="col-reviewed">
            <label class="pg-file-reviewed-label" title="Mark ${safeFile} as reviewed (Shift+Click for range)" onclick="onPgFileReviewedLabelClick(event, this)" onmouseenter="onPgFileReviewedLabelHover(event, this, true)" onmouseleave="onPgFileReviewedLabelHover(event, this, false)">
              <input type="checkbox" class="pg-file-reviewed-cb" data-cl="${c.number}" data-ps="${curPs}" data-file="${safeFile}" ${isRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this, event)" onclick="onPgFileReviewedCheckboxClick(event, this)">
              <span>Reviewed</span>
            </label>
          </td>
        </tr>`;
      }
      fileRowsHtml = rowsAcc;
    }

    if (fileList.length === 0 && hasAnyFilter) {
      let emptyMsg = `No files match filter "${escapeHtml(query)}".`;
      if (activeStatus && !query && !isFilterMissingActive && !isFilterCommentsActive && !activeExt && !activeSize) {
        const statusNames = { A: "added", M: "modified", D: "deleted", R: "renamed" };
        emptyMsg = `No ${statusNames[activeStatus] || activeStatus} files in change.`;
      } else if (activeExt && !query && !isFilterMissingActive && !isFilterCommentsActive && !activeStatus && !activeSize) {
        emptyMsg = `No ${escapeHtml(activeExt)} files in change.`;
      } else if (activeSize && !query && !isFilterMissingActive && !isFilterCommentsActive && !activeStatus && !activeExt) {
        const sizeNames = { XS: "extra small (XS)", S: "small (S)", M: "medium (M)", L: "large (L)", XL: "extra large (XL)" };
        emptyMsg = `No ${sizeNames[activeSize] || activeSize} files in change.`;
      } else if (isHideReviewed && (reviewedCount === rawFileList.length || hiddenReviewedCount === rawFileList.length) && !query && !isFilterMissingActive && !isFilterCommentsActive && !activeStatus && !activeExt && !activeSize) {
        emptyMsg = `All ${rawFileList.length} files in change are reviewed ✓`;
      } else if (isFilterMissingActive && query) {
        emptyMsg = `No files missing code owner approval match "${escapeHtml(query)}".`;
      } else if (isFilterMissingActive) {
        emptyMsg = "No files missing code owner approval ✓";
      }
      const clearBtnText = (isHideReviewed && !query && !isFilterMissingActive && !isFilterCommentsActive && !activeStatus && !activeExt && !activeSize) ? "Show reviewed files (h)" : "Clear filters";
      const clearBtnAction = (isHideReviewed && !query && !isFilterMissingActive && !isFilterCommentsActive && !activeStatus && !activeExt && !activeSize) ? `togglePgHideReviewed(${c.number}, false)` : `clearAllPgFileFilters(${c.number})`;
      fileRowsHtml = `<tr class="pg-file-row pg-file-row-empty"><td colspan="6" style="text-align: center; padding: 16px; color: var(--g-muted);">${emptyMsg} <button type="button" class="pg-btn-subtle" onclick="${clearBtnAction}" style="margin-left: 8px;">${clearBtnText}</button></td></tr>`;
    }

    const commitMsgRowHtml = showCommitMsg ? `<tr class="pg-file-row pg-magic-file-row ${isCommitMsgRev ? "is-reviewed" : ""} ${isCommitMsgFocused ? "is-focused" : ""} ${isCommitMsgCollapsed ? "is-collapsed" : ""}" tabindex="0" role="row" aria-label="Commit Message (/COMMIT_MSG)" data-cl="${c.number}" data-file="/COMMIT_MSG" data-owner-status="na" onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '/COMMIT_MSG')" onkeydown="onPgFileRowKeyDown(event, ${c.number}, ${curPs}, '/COMMIT_MSG')">
      <td class="col-status">
        <div style="display:inline-flex;align-items:center;gap:4px;">
          <button type="button" class="pg-file-diff-toggle-btn pg-magic-row-toggle-btn" title="${isCommitMsgCollapsed ? "Expand commit message (i)" : "Collapse commit message (i)"}" onclick="event.stopPropagation(); togglePgFileDiffBlock(event, ${c.number}, ${curPs}, '/COMMIT_MSG')">
            <span class="pg-file-chevron">${isCommitMsgCollapsed ? "▶" : "▼"}</span>
          </button>
          <span class="pg-file-status-badge status-M" title="Commit Message">M</span>
        </div>
      </td>
      <td class="col-path">
        <div class="pg-file-path-wrap">
          <a href="javascript:void(0)" class="pg-file-row-link" onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '/COMMIT_MSG')" title="View commit message">/COMMIT_MSG <span class="pg-magic-file-desc">Commit Message</span></a>
          <button type="button" class="pg-btn-subtle pg-btn-edit-magic-row" onclick="event.stopPropagation(); openPgCommitMsgEditor(${c.number})" title="Edit commit message in browser (Shortcut: e)">✏️ Edit</button>
        </div>
      </td>
      <td class="col-comments">${commitMsgBadgeHtml}</td>
      <td class="col-owners">${renderPgFileOwnerBadgeHtml({ status: "na", label: "—", title: "Not applicable" }, c.number, "/COMMIT_MSG", false)}</td>
      <td class="col-delta">
        <div class="pg-delta-cell-wrap">
          <span class="pg-file-size-badge size-msg" title="Commit Message">MSG</span>
          <span class="pg-file-delta"><span class="pg-delta-add">${commitMsgLines > 0 ? `${commitMsgLines} lines` : "Message"}</span></span>
          <span class="pg-delta-bar is-empty"></span>
        </div>
      </td>
      <td class="col-reviewed">
        <label class="pg-file-reviewed-label" title="Mark /COMMIT_MSG as reviewed (Shift+Click for range)" onclick="onPgFileReviewedLabelClick(event, this)" onmouseenter="onPgFileReviewedLabelHover(event, this, true)" onmouseleave="onPgFileReviewedLabelHover(event, this, false)">
          <input type="checkbox" class="pg-file-reviewed-cb" data-cl="${c.number}" data-ps="${curPs}" data-file="/COMMIT_MSG" ${isCommitMsgRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this, event)" onclick="onPgFileReviewedCheckboxClick(event, this)">
          <span>Reviewed</span>
        </label>
      </td>
    </tr>` : "";

    const mergeListRowHtml = showMergeList ? `<tr class="pg-file-row pg-magic-file-row ${isMergeListRev ? "is-reviewed" : ""} ${isMergeListFocused ? "is-focused" : ""} ${isMergeListCollapsed ? "is-collapsed" : ""}" tabindex="0" role="row" aria-label="Merge List (/MERGE_LIST)" data-cl="${c.number}" data-file="/MERGE_LIST" data-owner-status="na" onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '/MERGE_LIST')" onkeydown="onPgFileRowKeyDown(event, ${c.number}, ${curPs}, '/MERGE_LIST')">
      <td class="col-status">
        <div style="display:inline-flex;align-items:center;gap:4px;">
          <button type="button" class="pg-file-diff-toggle-btn pg-magic-row-toggle-btn" title="${isMergeListCollapsed ? "Expand merge list (i)" : "Collapse merge list (i)"}" onclick="event.stopPropagation(); togglePgFileDiffBlock(event, ${c.number}, ${curPs}, '/MERGE_LIST')">
            <span class="pg-file-chevron">${isMergeListCollapsed ? "▶" : "▼"}</span>
          </button>
          <span class="pg-file-status-badge status-M" title="Merge List">M</span>
        </div>
      </td>
      <td class="col-path">
        <a href="javascript:void(0)" class="pg-file-row-link" onclick="onPgFileRowClick(event, ${c.number}, ${curPs}, '/MERGE_LIST')" title="View merge list">/MERGE_LIST <span class="pg-magic-file-desc">Merge List</span></a>
      </td>
      <td class="col-comments">${mergeListBadgeHtml}</td>
      <td class="col-owners">${renderPgFileOwnerBadgeHtml({ status: "na", label: "—", title: "Not applicable" }, c.number, "/MERGE_LIST", false)}</td>
      <td class="col-delta">
        <div class="pg-delta-cell-wrap">
          <span class="pg-file-size-badge size-merge" title="Merge List">MERGE</span>
          <span class="pg-file-delta"><span class="pg-delta-add">${mergeCommits.length} commit${mergeCommits.length === 1 ? "" : "s"}</span></span>
          <span class="pg-delta-bar is-empty"></span>
        </div>
      </td>
      <td class="col-reviewed">
        <label class="pg-file-reviewed-label" title="Mark /MERGE_LIST as reviewed (Shift+Click for range)" onclick="onPgFileReviewedLabelClick(event, this)" onmouseenter="onPgFileReviewedLabelHover(event, this, true)" onmouseleave="onPgFileReviewedLabelHover(event, this, false)">
          <input type="checkbox" class="pg-file-reviewed-cb" data-cl="${c.number}" data-ps="${curPs}" data-file="/MERGE_LIST" ${isMergeListRev ? "checked" : ""} onchange="onPgFileReviewedCheckboxChanged(this, event)" onclick="onPgFileReviewedCheckboxClick(event, this)">
          <span>Reviewed</span>
        </label>
      </td>
    </tr>` : "";

    const listOutHtml = `<div class="pg-file-list-table-wrap">
      <div class="pg-file-list-header-bar">
        <div class="pg-file-filter-wrap" id="pgFileFilterWrap-${c.number}">
          <span class="pg-file-filter-icon" aria-hidden="true">🔍</span>
          <input
            type="text"
            id="pgFileFilterInput-${c.number}"
            class="pg-file-filter-input"
            placeholder="Filter files (e.g. *.cc, src/, test) — Shift+F"
            value="${escapeHtml(query)}"
            oninput="onPgFileFilterInput(${c.number}, this.value)"
            onkeydown="onPgFileFilterKeyDown(event, ${c.number})"
            aria-label="Filter files"
            autocomplete="off"
            spellcheck="false"
          />
          <button type="button" class="pg-file-filter-clear-btn" id="pgFileFilterClearBtn-${c.number}" style="${query ? "" : "display:none;"}" onclick="clearPgFileFilter(${c.number})" title="Clear filter (Esc)">✕</button>
        </div>
        <div class="pg-file-filter-presets" id="pgFileFilterPresets-${c.number}">
          <button type="button" class="pg-filter-preset-btn ${!hasAnyFilter ? "is-active" : ""}" id="btnFilterPresetAll-${c.number}" onclick="clearAllPgFileFilters(${c.number})" title="Show all files in change">All (${rawFileList.length})</button>
          <button type="button" class="pg-filter-preset-btn ${isHideReviewed ? "is-active" : ""}" id="btnFilterPresetUnreviewed-${c.number}" onclick="togglePgHideReviewed(${c.number})" title="Filter files: show only unreviewed files (Shortcut: h)">Unreviewed (${unreviewedCount})</button>
          ${statusCounts.A > 0 ? `<button type="button" class="pg-filter-preset-btn pg-status-preset-btn status-A ${activeStatus === "A" ? "is-active" : ""}" id="btnFilterPresetAdded-${c.number}" onclick="togglePgFileStatusFilter(${c.number}, 'A')" title="Filter files: show only added files (A) · Alt+S to cycle">+ Added (${statusCounts.A})</button>` : ""}
          ${statusCounts.M > 0 && (statusCounts.A > 0 || statusCounts.D > 0 || statusCounts.R > 0) ? `<button type="button" class="pg-filter-preset-btn pg-status-preset-btn status-M ${activeStatus === "M" ? "is-active" : ""}" id="btnFilterPresetModified-${c.number}" onclick="togglePgFileStatusFilter(${c.number}, 'M')" title="Filter files: show only modified files (M) · Alt+S to cycle">M Modified (${statusCounts.M})</button>` : ""}
          ${statusCounts.D > 0 ? `<button type="button" class="pg-filter-preset-btn pg-status-preset-btn status-D ${activeStatus === "D" ? "is-active" : ""}" id="btnFilterPresetDeleted-${c.number}" onclick="togglePgFileStatusFilter(${c.number}, 'D')" title="Filter files: show only deleted files (D) · Alt+S to cycle">- Deleted (${statusCounts.D})</button>` : ""}
          ${statusCounts.R > 0 ? `<button type="button" class="pg-filter-preset-btn pg-status-preset-btn status-R ${activeStatus === "R" ? "is-active" : ""}" id="btnFilterPresetRenamed-${c.number}" onclick="togglePgFileStatusFilter(${c.number}, 'R')" title="Filter files: show only renamed files (R) · Alt+S to cycle">→ Renamed (${statusCounts.R})</button>` : ""}
          ${statusCounts.C > 0 ? `<button type="button" class="pg-filter-preset-btn pg-status-preset-btn status-C ${activeStatus === "C" ? "is-active" : ""}" id="btnFilterPresetCopied-${c.number}" onclick="togglePgFileStatusFilter(${c.number}, 'C')" title="Filter files: show only copied files (C) · Alt+S to cycle">📋 Copied (${statusCounts.C})</button>` : ""}
          ${extPresetsHtml}
          ${sizePresetsHtml}
          ${unresolvedCount > 0 ? `<button type="button" class="pg-filter-preset-btn ${commentsMode === "unresolved" ? "is-active" : ""}" id="btnFilterPresetUnresolved-${c.number}" onclick="togglePgCommentsFilterMode(${c.number}, 'unresolved')" title="Show files with unresolved comments or drafts (Shortcut: Shift+H)">⚠️ Unresolved (${unresolvedCount})</button>` : ""}
          ${commentedFilesCount > 0 && (commentedFilesCount !== unresolvedCount || unresolvedCount === 0) ? `<button type="button" class="pg-filter-preset-btn ${commentsMode === "comments" ? "is-active" : ""}" id="btnFilterPresetComments-${c.number}" onclick="togglePgCommentsFilterMode(${c.number}, 'comments')" title="Show all files with comments or drafts (Shortcut: Shift+H)">💬 Comments (${commentedFilesCount})</button>` : ""}
          ${draftsFilesCount > 0 && draftsFilesCount !== unresolvedCount ? `<button type="button" class="pg-filter-preset-btn ${commentsMode === "drafts" ? "is-active" : ""}" id="btnFilterPresetDrafts-${c.number}" onclick="togglePgCommentsFilterMode(${c.number}, 'drafts')" title="Show files with unpublished drafts">📝 Drafts (${draftsFilesCount})</button>` : ""}
          ${missingOwnerCount > 0 ? `<button type="button" class="pg-filter-preset-btn ${isFilterMissingActive ? "is-active" : ""}" id="btnFilterPresetOwners-${c.number}" onclick="togglePgOwnersFilter(${c.number})" title="Show files missing code owner approval">Missing Owner (${missingOwnerCount})</button>` : ""}
        </div>
        <div class="pg-file-list-filter-chips">
          ${query ? `<span class="pg-file-filter-chip">Filter: "${escapeHtml(query)}" <button type="button" class="pg-file-filter-chip-remove" onclick="clearPgFileFilter(${c.number})" title="Clear keyword filter">✕</button></span>` : ""}
          ${activeStatus ? `<span class="pg-file-filter-chip pg-filter-chip-status">Status: ${activeStatus === "A" ? "Added (A)" : (activeStatus === "D" ? "Deleted (D)" : (activeStatus === "R" ? "Renamed (R)" : (activeStatus === "C" ? "Copied (C)" : "Modified (M)")))} <button type="button" class="pg-file-filter-chip-remove" onclick="setPgFileStatusFilter(${c.number}, '')" title="Clear status filter">✕</button></span>` : ""}
          ${activeExt ? `<span class="pg-file-filter-chip pg-filter-chip-extension">Extension: ${escapeHtml(activeExt)} <button type="button" class="pg-file-filter-chip-remove" onclick="setPgFileExtensionFilter(${c.number}, '')" title="Clear extension filter">✕</button></span>` : ""}
          ${activeSize ? `<span class="pg-file-filter-chip pg-filter-chip-size">Size: ${escapeHtml(activeSize)} <button type="button" class="pg-file-filter-chip-remove" onclick="setPgFileSizeFilter(${c.number}, '')" title="Clear size filter">✕</button></span>` : ""}
          ${isFilterMissingActive ? `<span class="pg-file-filter-chip">Missing owner approval <button type="button" class="pg-file-filter-chip-remove" onclick="togglePgOwnersFilter(${c.number})" title="Clear owner filter">✕</button></span>` : ""}
          ${isFilterCommentsActive ? `<span class="pg-file-filter-chip pg-filter-chip-comments">${commentsMode === "drafts" ? "Drafts only" : (commentsMode === "comments" ? "Comments only" : "Unresolved / drafts only")} <button type="button" class="pg-file-filter-chip-remove" onclick="clearPgCommentsFilter(${c.number})" title="Clear comments filter">✕</button></span>` : ""}
          ${isHideReviewed && hiddenReviewedCount > 0 ? `<span class="pg-file-filter-chip pg-filter-chip-reviewed" title="${hiddenReviewedCount} reviewed files hidden from list">Hide reviewed (${hiddenReviewedCount} hidden) <button type="button" class="pg-file-filter-chip-remove" onclick="togglePgHideReviewed(${c.number}, false)" title="Show reviewed files">✕</button></span>` : ""}
        </div>
        <label class="pg-hide-reviewed-toggle-wrap" title="Hide reviewed files from list and diffs (Shortcut: h)">
          <input
            type="checkbox"
            id="pgHideReviewedCb-${c.number}"
            class="pg-hide-reviewed-cb"
            ${isHideReviewed ? "checked" : ""}
            onchange="togglePgHideReviewed(${c.number}, this.checked)"
            aria-label="Hide reviewed files (Shortcut: h)"
          />
          <span class="pg-hide-reviewed-label-text">Hide reviewed</span>
          <span class="pg-filter-key-hint">h</span>
        </label>
        <div class="pg-file-view-mode-toggle" id="pgFileViewModeToggle-${c.number}">
          <span class="pg-view-mode-label">View:</span>
          <div class="pg-segmented-ctrl">
            <button
              type="button"
              class="pg-btn-view-mode ${!isTree ? "is-active" : ""}"
              id="btnFileModeFlat-${c.number}"
              onclick="setPgFileTreeMode(${c.number}, false)"
              title="View files as flat list (y)"
              aria-pressed="${!isTree}"
            >📋 Flat</button>
            <button
              type="button"
              class="pg-btn-view-mode ${isTree ? "is-active" : ""}"
              id="btnFileModeTree-${c.number}"
              onclick="setPgFileTreeMode(${c.number}, true)"
              title="View files as directory tree (y)"
              aria-pressed="${isTree}"
            >🌳 Tree</button>
          </div>
          ${isTree ? `<button type="button" class="pg-btn-toggle-dirs ${areAllPgDirsCollapsed(c.number) ? "is-all-collapsed" : ""}" id="btnToggleAllDirs-${c.number}" onclick="toggleAllPgDirsCollapse(${c.number})" title="${areAllPgDirsCollapsed(c.number) ? "Expand all directories in tree view (Alt+Y)" : "Collapse all directories in tree view (Alt+Y)"}">${areAllPgDirsCollapsed(c.number) ? "⤢ Expand dirs" : "⤡ Collapse dirs"}</button>` : ""}
        </div>
        <div class="pg-file-list-sort-controls">
          <span class="pg-sort-label">Sort:</span>
          <div class="pg-sort-dropdown-wrap">
            <select id="pgFileSortSelect-${c.number}" class="pg-file-sort-select" onchange="onPgFileSortSelectChange(${c.number}, this.value)" aria-label="Sort files by column">
              <option value="path:asc" ${sortVal === "path:asc" ? "selected" : ""}>Path (A → Z)</option>
              <option value="path:desc" ${sortVal === "path:desc" ? "selected" : ""}>Path (Z → A)</option>
              <option value="delta:desc" ${sortVal === "delta:desc" ? "selected" : ""}>Delta / Size (Largest first)</option>
              <option value="delta:asc" ${sortVal === "delta:asc" ? "selected" : ""}>Delta / Size (Smallest first)</option>
              <option value="comments:desc" ${sortVal === "comments:desc" ? "selected" : ""}>Comments (Most comments first)</option>
              <option value="owners:asc" ${sortVal === "owners:asc" ? "selected" : ""}>Owners (Missing approval first)</option>
              <option value="status:asc" ${sortVal === "status:asc" ? "selected" : ""}>Status (A → M → R → D)</option>
              <option value="reviewed:asc" ${sortVal === "reviewed:asc" ? "selected" : ""}>Reviewed (Unreviewed first)</option>
            </select>
          </div>
          ${!isDefaultSort ? `<button type="button" class="pg-btn-reset-sort" id="btnResetFileSort-${c.number}" onclick="resetPgFileSort(${c.number})" title="Reset to default sorting (Path A→Z)">✕ Reset sort</button>` : ""}
          ${renderPgCopyFilePathsDropdownHtml(c, fileList, rawFileList, revSet)}
          <button
            type="button"
            class="pg-unreviewed-filter-btn ${isHideReviewed ? "active" : ""}"
            id="btnFilterUnreviewed-${c.number}"
            onclick="togglePgHideReviewed(${c.number})"
            title="Filter files: show only unreviewed files (${unreviewedCount} unreviewed) (Shortcut: h)"
          >${unreviewedBtnLabel}</button>
          <button
            type="button"
            class="pg-comments-filter-btn ${isFilterCommentsActive ? "active" : ""}"
            id="btnFilterComments-${c.number}"
            onclick="cyclePgCommentsFilter(${c.number})"
            title="Filter files with comments: cycle All / Unresolved / All comments (Shortcut: Shift+H)"
          >${commentsMode === "drafts" ? "📝 Drafts only" : (commentsMode === "comments" ? "💬 Commented files" : "⚠️ Unresolved files")}</button>
          <button
            type="button"
            class="pg-btn-subtle pg-btn-mark-all-reviewed ${isAllRev ? "is-all-reviewed" : ""}"
            id="btnMarkAllReviewed-${c.number}"
            onclick="togglePgMarkAllReviewed(${c.number})"
            title="${isAllRev ? "Unmark all files as reviewed (Shift+V)" : "Mark all files as reviewed (Shift+V)"}"
          >${markAllBtnLabel}</button>
          <button
            type="button"
            class="pg-btn-subtle pg-btn-expand-all-files ${allFilesExpanded ? "is-disabled" : ""}"
            id="btnExpandAllFiles-${c.number}"
            data-cl="${c.number}"
            onclick="expandAllPgFileDiffs(${c.number}, ${curPs})"
            title="${allFilesExpanded ? "All file diffs are already expanded (Shift+I)" : `Expand all ${collapsedFilesCount} collapsed file diffs (Shortcut: Shift+I)`}"
            ${allFilesExpanded ? "disabled" : ""}
          ><span class="pg-btn-icon" aria-hidden="true">☷</span> Expand all <span class="pg-shortcut-hint">Shift+I</span></button>
          <button
            type="button"
            class="pg-btn-subtle pg-btn-collapse-all-files ${allFilesCollapsed ? "is-disabled" : ""}"
            id="btnCollapseAllFiles-${c.number}"
            data-cl="${c.number}"
            onclick="collapseAllPgFileDiffs(${c.number}, ${curPs})"
            title="${allFilesCollapsed ? "All file diffs are already collapsed (Shift+E)" : `Collapse all ${expandedFilesCount} expanded file diffs (Shortcut: Shift+E)`}"
            ${allFilesCollapsed ? "disabled" : ""}
          ><span class="pg-btn-icon" aria-hidden="true">☰</span> Collapse all <span class="pg-shortcut-hint">Shift+E</span></button>
          <button
            type="button"
            class="pg-btn-subtle pg-btn-expand-unreviewed"
            id="btnExpandUnreviewed-${c.number}"
            onclick="expandPgUnreviewedFileDiffs(${c.number}, ${curPs})"
            title="Expand diffs for only unreviewed files, collapsing reviewed ones"
          >⚡ Expand unreviewed</button>
          <button
            type="button"
            class="pg-btn-subtle pg-btn-next-unreviewed"
            id="btnNextUnreviewed-${c.number}"
            onclick="navigatePgUnreviewedFileDiff('next', null, ${c.number})"
            title="Jump to next unreviewed file diff (Shortcut: Alt+])"
            style="${unreviewedCount > 0 ? "" : "display:none;"}"
          >⚡ Next unreviewed <span class="pg-unreviewed-pill-count">${unreviewedCount}</span></button>
          <span class="pg-all-reviewed-badge" id="badgeAllReviewed-${c.number}" style="${unreviewedCount === 0 && rawFileList.length > 0 ? "" : "display:none;"}" title="All files in this change have been marked as reviewed">✓ All reviewed</span>
          ${commentedFilesCount > 0 ? `
          <button
            type="button"
            class="pg-btn-subtle pg-btn-expand-comments"
            id="btnExpandComments-${c.number}"
            onclick="expandPgCommentsFileDiffs(${c.number}, ${curPs})"
            title="Expand diffs for files with comments or drafts, collapsing files without comments"
          >💬 Expand commented</button>` : ""}
        </div>
      </div>
      ${filterNoticeHtml}
      ${renderPgFileListRangeBarHtml(c.number)}
      <table class="pg-file-list-table" aria-label="Change Files List">
        <thead>
          <tr>
            <th class="col-status is-sortable ${fileSort.col === "status" ? "is-sorted" : ""}" onclick="togglePgFileSort(${c.number}, 'status')" title="Sort by status (Added, Modified, Deleted, Renamed)">
              <span class="pg-th-content">Status ${renderSortIndicator("status")}</span>
            </th>
            <th class="col-path is-sortable ${fileSort.col === "path" ? "is-sorted" : ""}" onclick="togglePgFileSort(${c.number}, 'path')" title="Sort by file path">
              <span class="pg-th-content">Path ${renderSortIndicator("path")}</span>
            </th>
            <th class="col-comments is-sortable ${fileSort.col === "comments" ? "is-sorted" : ""}" onclick="togglePgFileSort(${c.number}, 'comments')" title="Sort by comments count">
              <span class="pg-th-content">Comments ${renderSortIndicator("comments")}</span>
            </th>
            <th class="col-owners is-sortable ${fileSort.col === "owners" ? "is-sorted" : ""}" onclick="togglePgFileSort(${c.number}, 'owners')" title="Sort by code owner approval status">
              <span class="pg-th-content">Owners ${renderSortIndicator("owners")}</span>
            </th>
            <th class="col-delta is-sortable ${(fileSort.col === "delta" || fileSort.col === "size") ? "is-sorted" : ""}" onclick="togglePgFileSort(${c.number}, 'delta')" title="Sort by delta / change size">
              <span class="pg-th-content">Delta / Size ${renderSortIndicator("delta")}</span>
            </th>
            <th class="col-reviewed is-sortable ${fileSort.col === "reviewed" ? "is-sorted" : ""}" onclick="onPgReviewedHeaderClick(event, ${c.number})" title="Sort by reviewed status or mark all files reviewed">
              <div class="pg-th-content pg-th-reviewed-content">
                <label class="pg-master-reviewed-label" title="${isAllRev ? `Mark all ${visibleCount} files as unreviewed (Shift+V)` : `Mark all ${visibleCount} files as reviewed (Shift+V)`}" onclick="event.stopPropagation()">
                  <input
                    type="checkbox"
                    id="pgMasterReviewedCb-${c.number}"
                    class="pg-master-reviewed-cb"
                    data-cl="${c.number}"
                    data-ps="${curPs}"
                    ${isAllRev ? "checked" : ""}
                    ${someRev ? "data-indeterminate=\"true\"" : ""}
                    onclick="event.stopPropagation()"
                    onchange="onPgMasterReviewedCheckboxChanged(this)"
                    aria-label="Mark all files as reviewed or unreviewed (Shift+V)"
                  />
                </label>
                <span class="pg-th-reviewed-text">Reviewed ${renderSortIndicator("reviewed")}</span>
              </div>
            </th>
          </tr>
        </thead>
        <tbody>
          ${commitMsgRowHtml}
          ${mergeListRowHtml}
          ${fileRowsHtml}
        </tbody>
        <tfoot>
          <tr class="pg-file-list-footer-row">
            <td class="col-status"></td>
            <td class="col-path">
              <span class="pg-footer-total-files">${rawFileList.length} file${rawFileList.length === 1 ? "" : "s"}</span>
              ${visibleCount !== rawFileList.length ? `<span class="pg-footer-filtered-note">(${visibleCount} shown)</span>` : ""}
              <button type="button" class="pg-file-copy-btn pg-footer-copy-paths-btn" id="btnFooterCopyPaths-${c.number}" onclick="copyPgFilePaths(${c.number}, 'all', this)" title="Copy all ${rawFileList.length} file paths to clipboard (Shortcut: Shift+P on row)"><span aria-hidden="true">📋</span></button>
            </td>
            <td class="col-comments">
              ${totalCommentsCount > 0 ? `<span class="pg-footer-comments" title="${totalCommentsCount} total comment(s), ${totalUnresolvedCount} unresolved">💬 ${totalCommentsCount}${totalUnresolvedCount > 0 ? ` (${totalUnresolvedCount} unres)` : ""}</span>` : `<span class="pg-footer-empty">—</span>`}
            </td>
            <td class="col-owners">
              ${missingOwnerCount > 0 ? `<span class="pg-footer-owner-missing" title="${missingOwnerCount} file(s) missing owner approval">✕ ${missingOwnerCount} missing</span>` : `<span class="pg-footer-owner-ok" title="All files approved">✓ All ok</span>`}
            </td>
            <td class="col-delta">
              <div class="pg-delta-cell-wrap">
                <span class="pg-file-delta pg-footer-delta">
                  <span class="pg-delta-add">+${totalAdditions < 1000 ? totalAdditions : totalAdditions.toLocaleString()}</span>, <span class="pg-delta-del">-${totalDeletions < 1000 ? totalDeletions : totalDeletions.toLocaleString()}</span>
                </span>
                <span class="pg-delta-bar" title="Total changes: +${totalAdditions}, -${totalDeletions} (${(totalAdditions + totalDeletions) < 1000 ? (totalAdditions + totalDeletions) : (totalAdditions + totalDeletions).toLocaleString()} lines changed)">
                  <span class="pg-delta-bar-fill" style="width: 100%;">
                    <span class="pg-delta-bar-add" style="width: ${totalAdditions + totalDeletions > 0 ? Math.round((totalAdditions / (totalAdditions + totalDeletions)) * 100) : 50}%;"></span>
                    <span class="pg-delta-bar-del" style="width: ${totalAdditions + totalDeletions > 0 ? 100 - Math.round((totalAdditions / (totalAdditions + totalDeletions)) * 100) : 50}%;"></span>
                  </span>
                </span>
              </div>
            </td>
            <td class="col-reviewed">
              <span class="pg-footer-reviewed" title="${reviewedCount} of ${rawFileList.length} files marked as reviewed">
                ${reviewedCount}/${rawFileList.length} (${Math.round((reviewedCount / (rawFileList.length || 1)) * 100)}%)
              </span>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>`;
    if (fileListCacheKey) {
      setPgBoundedMapEntry(_pgFileListHtmlCache, fileListCacheKey, listOutHtml);
      recordFlSlot(listOutHtml);
    }
    return listOutHtml;
  }

  if (typeof window !== "undefined") {
    window.getPgReviewedSet = getPgReviewedSet;
    window.savePgReviewedSet = savePgReviewedSet;
    window.isPgFileReviewed = isPgFileReviewed;
    window.setPgFileReviewed = setPgFileReviewed;
    window.syncPgReviewedFilesFromServer = syncPgReviewedFilesFromServer;
    window.syncPgFileListReviewedDom = syncPgFileListReviewedDom;
    window.syncPgFileReviewedToServer = syncPgFileReviewedToServer;
    window.syncPgBatchFilesReviewedToServer = syncPgBatchFilesReviewedToServer;
    window.fetchPgReviewedFilesFromServer = fetchPgReviewedFilesFromServer;
    window.getPgCollapsedSet = getPgCollapsedSet;
    window.isPgFileCollapsed = isPgFileCollapsed;
    window.setPgFileCollapsed = setPgFileCollapsed;
    window.togglePgFileDiffBlock = togglePgFileDiffBlock;
    window.toggleAllPgFileDiffs = toggleAllPgFileDiffs;
    window.expandAllPgFileDiffs = expandAllPgFileDiffs;
    window.collapseAllPgFileDiffs = collapseAllPgFileDiffs;
    window.onPgFileRowKeyDown = onPgFileRowKeyDown;
    window.getPgFileStatus = getPgFileStatus;
    window.getPgFileSimilarity = getPgFileSimilarity;
    window.renderPgFileRenamePillHtml = renderPgFileRenamePillHtml;
    window.renderPgFileStatusBadgeHtml = renderPgFileStatusBadgeHtml;
    window.getPgFileStatusFilter = getPgFileStatusFilter;
    window.setPgFileStatusFilter = setPgFileStatusFilter;
    window.togglePgFileStatusFilter = togglePgFileStatusFilter;
    window.cyclePgFileStatusFilter = cyclePgFileStatusFilter;
    window.onPgFileStatusBadgeClick = onPgFileStatusBadgeClick;
    window.onPgFileStatusBadgeKeyDown = onPgFileStatusBadgeKeyDown;
    window.getPgFileExtension = getPgFileExtension;
    window.getPgFileExtensionFilter = getPgFileExtensionFilter;
    window.setPgFileExtensionFilter = setPgFileExtensionFilter;
    window.togglePgFileExtensionFilter = togglePgFileExtensionFilter;
    window.cyclePgFileExtensionFilter = cyclePgFileExtensionFilter;
    window.renderPgFileExtBadgeHtml = renderPgFileExtBadgeHtml;
    window.onPgFileExtBadgeClick = onPgFileExtBadgeClick;
    window.onPgFileExtBadgeKeyDown = onPgFileExtBadgeKeyDown;
    window.getPgFileSizeFilter = getPgFileSizeFilter;
    window.setPgFileSizeFilter = setPgFileSizeFilter;
    window.togglePgFileSizeFilter = togglePgFileSizeFilter;
    window.cyclePgFileSizeFilter = cyclePgFileSizeFilter;
    window.onPgFileSizeBadgeClick = onPgFileSizeBadgeClick;
    window.onPgFileSizeBadgeKeyDown = onPgFileSizeBadgeKeyDown;
    window.getPgFileOwnerStatus = getPgFileOwnerStatus;
    window.renderPgFileOwnerBadgeHtml = renderPgFileOwnerBadgeHtml;
    window.renderPgOwnerTooltipContentHtml = renderPgOwnerTooltipContentHtml;
    window.ensurePgOwnerTooltipEl = ensurePgOwnerTooltipEl;
    window.positionPgOwnerTooltip = positionPgOwnerTooltip;
    window.showPgOwnerTooltip = showPgOwnerTooltip;
    window.hidePgOwnerTooltip = hidePgOwnerTooltip;
    window.scheduleHidePgOwnerTooltip = scheduleHidePgOwnerTooltip;
    window.cancelHidePgOwnerTooltip = cancelHidePgOwnerTooltip;
    window.onPgOwnerBadgeMouseEnter = onPgOwnerBadgeMouseEnter;
    window.onPgOwnerBadgeMouseLeave = onPgOwnerBadgeMouseLeave;
    window.onPgOwnerBadgeFocus = onPgOwnerBadgeFocus;
    window.onPgOwnerBadgeBlur = onPgOwnerBadgeBlur;
    window.openPgCodeOwnersDialog = openPgCodeOwnersDialog;
    window.closePgCodeOwnersDialog = closePgCodeOwnersDialog;
    window.onPgCodeOwnersBackdropClick = onPgCodeOwnersBackdropClick;
    window.onPgFileOwnerBadgeClick = onPgFileOwnerBadgeClick;
    window.onPgFileOwnerBadgeKeyDown = onPgFileOwnerBadgeKeyDown;
    window.onPgDirOwnerBadgeClick = onPgDirOwnerBadgeClick;
    window.onPgDirOwnerBadgeKeyDown = onPgDirOwnerBadgeKeyDown;
    window.copyPgOwnersText = copyPgOwnersText;
    window.copyPgOwnersEmails = copyPgOwnersEmails;
    window.togglePgMissingOwnersFilterFromDialog = togglePgMissingOwnersFilterFromDialog;
    window.addPgOwnerAsReviewer = addPgOwnerAsReviewer;
    window.renderPgCodeOwnersModalContent = renderPgCodeOwnersModalContent;
    window.getPgFileQueryFilter = getPgFileQueryFilter;
    window.setPgFileQueryFilter = setPgFileQueryFilter;
    window.matchesPgFileFilter = matchesPgFileFilter;
    window.isPgFuzzySubsequence = isPgFuzzySubsequence;
    window.highlightPgFilePathMatch = highlightPgFilePathMatch;
    window.getPgHideReviewedPreference = getPgHideReviewedPreference;
    window.setPgHideReviewedPreference = setPgHideReviewedPreference;
    window.isPgHideReviewedActive = isPgHideReviewedActive;
    window.setPgHideReviewed = setPgHideReviewed;
    window.togglePgHideReviewed = togglePgHideReviewed;
    window.clearPgFileFilter = clearPgFileFilter;
    window.clearAllPgFileFilters = clearAllPgFileFilters;
    window.onPgFileFilterInput = onPgFileFilterInput;
    window.onPgFileFilterKeyDown = onPgFileFilterKeyDown;
    window.applyPgFileFiltersToDom = applyPgFileFiltersToDom;
    window.reanchorPgFocusedFile = reanchorPgFocusedFile;
    window.isPgFileFilterActive = isPgFileFilterActive;
    window.isPgOwnerFilterActive = isPgOwnerFilterActive;
    window.togglePgOwnersFilter = togglePgOwnersFilter;
    window.getPendingDraftsForFile = getPendingDraftsForFile;
    window.getPgFileCommentStats = getPgFileCommentStats;
    window.renderPgFileCommentsBadgesHtml = renderPgFileCommentsBadgesHtml;
    window.getPgCommentThreadNavInfo = getPgCommentThreadNavInfo;
    window.renderPgCommentThreadNavHtml = renderPgCommentThreadNavHtml;
    window.navigatePgFileCommentThread = navigatePgFileCommentThread;
    window.navigatePgCommentThread = navigatePgCommentThread;
    window.onPgCommentThreadKeyDown = onPgCommentThreadKeyDown;
    window.buildServerThreadInlineItem = buildServerThreadInlineItem;
    window.getInlineCommentsForFile = getInlineCommentsForFile;
    window.scrollToPgFileHeader = scrollToPgFileHeader;
    window.onPgFileUnresolvedCommentsClick = onPgFileUnresolvedCommentsClick;
    window.onPgFileDraftCommentsClick = onPgFileDraftCommentsClick;
    window.isPgCommentsFilterActive = isPgCommentsFilterActive;
    window.getPgCommentsFilterMode = getPgCommentsFilterMode;
    window.setPgCommentsFilterMode = setPgCommentsFilterMode;
    window.togglePgCommentsFilter = togglePgCommentsFilter;
    window.togglePgCommentsFilterMode = togglePgCommentsFilterMode;
    window.cyclePgCommentsFilter = cyclePgCommentsFilter;
    window.clearPgCommentsFilter = clearPgCommentsFilter;
    window.renderPgOwnersSummaryHtml = renderPgOwnersSummaryHtml;
    window.getPgFileChecksStatus = getPgFileChecksStatus;
    window.formatPgTargetShortName = formatPgTargetShortName;
    window.renderPgFileChecksChipHtml = renderPgFileChecksChipHtml;
    window.onPgFileChecksChipClick = onPgFileChecksChipClick;
    window.selectPgChecksStatusFilter = selectPgChecksStatusFilter;
    window.getPgFileSizeInfo = getPgFileSizeInfo;
    window.renderPgFileSizeBadgeHtml = renderPgFileSizeBadgeHtml;
    window.renderPgFileDeltaBarHtml = renderPgFileDeltaBarHtml;
    window.renderPgFileDeltaNumbersHtml = renderPgFileDeltaNumbersHtml;
    window.getPgFileSort = getPgFileSort;
    window.setPgFileSort = setPgFileSort;
    window.getPgSortedFileList = getPgSortedFileList;
    window.togglePgFileSort = togglePgFileSort;
    window.resetPgFileSort = resetPgFileSort;
    window.onPgFileSortSelectChange = onPgFileSortSelectChange;
    window.cyclePgFileSort = cyclePgFileSort;
    window.renderPgFileListHtml = renderPgFileListHtml;
    window.onPgFileRowClick = onPgFileRowClick;
    window.onPgFileHeaderClick = onPgFileHeaderClick;
    window.onPgFileReviewedCheckboxChanged = onPgFileReviewedCheckboxChanged;
    window.onPgFileReviewedCheckboxClick = onPgFileReviewedCheckboxClick;
    window.onPgFileReviewedLabelClick = onPgFileReviewedLabelClick;
    window.onPgFileReviewedLabelHover = onPgFileReviewedLabelHover;
    window.clearPgFileListRangeHover = clearPgFileListRangeHover;
    window.clearPgFileListRangeSelection = clearPgFileListRangeSelection;
    window.updatePgFileListRangeSelection = updatePgFileListRangeSelection;
    window.updatePgFileListDragPill = updatePgFileListDragPill;
    window.removePgFileListDragPill = removePgFileListDragPill;
    window.cancelPgFileListDrag = cancelPgFileListDrag;
    window.initPgFileListRangeDragListeners = initPgFileListRangeDragListeners;
    window.updatePgDashboardDragPill = updatePgDashboardDragPill;
    window.removePgDashboardDragPill = removePgDashboardDragPill;
    window.clearPgDashboardRangeHover = clearPgDashboardRangeHover;
    window.cancelPgDashboardDrag = cancelPgDashboardDrag;
    window.initPgDashboardRangeDragListeners = initPgDashboardRangeDragListeners;
    window.syncPgFileListRangeBar = syncPgFileListRangeBar;
    window.renderPgFileListRangeBarHtml = renderPgFileListRangeBarHtml;
    window.executePgRangeAction = executePgRangeAction;
    window.setPgFilesReviewedRange = setPgFilesReviewedRange;
    window.getPgFilteredAndSortedFiles = getPgFilteredAndSortedFiles;
    window.getPgVisibleFileList = getPgVisibleFileList;
    window.getPgAllVisibleFiles = getPgAllVisibleFiles;
    window.focusPgFileRow = focusPgFileRow;
    window.navigatePgFileListCursor = navigatePgFileListCursor;
    window.getPgUnreviewedFiles = getPgUnreviewedFiles;
    window.jumpToFirstUnreviewedFile = jumpToFirstUnreviewedFile;
    window.navigatePgUnreviewedFileDiff = navigatePgUnreviewedFileDiff;
    window.markActiveFileReviewedAndGoToNextUnreviewed = markActiveFileReviewedAndGoToNextUnreviewed;
    window.togglePgFocusedFileReviewed = togglePgFocusedFileReviewed;
    window.openFocusedPgFileDiff = openFocusedPgFileDiff;
    window.expandPgUnreviewedFileDiffs = expandPgUnreviewedFileDiffs;
    window.expandPgCommentsFileDiffs = expandPgCommentsFileDiffs;
    window.markAllPgFilesReviewed = markAllPgFilesReviewed;
    window.togglePgMarkAllReviewed = togglePgMarkAllReviewed;
    window.onPgMasterReviewedCheckboxChanged = onPgMasterReviewedCheckboxChanged;
    window.onPgReviewedHeaderClick = onPgReviewedHeaderClick;
    window.syncPgMasterReviewedCheckbox = syncPgMasterReviewedCheckbox;
    window.syncPgMarkAllReviewedButton = syncPgMarkAllReviewedButton;
    window.syncPgReviewedSummary = syncPgReviewedSummary;
    window.syncPgExpandAllButtonState = syncPgExpandAllButtonState;
    window.scrollToFileDiff = scrollToFileDiff;
    window.scrollToCommitMessageBox = scrollToCommitMessageBox;
    window.jumpToPgCommitMessage = jumpToPgCommitMessage;
    window.toggleCommitMessage = toggleCommitMessage;
    window.uncollapseCommitMessage = uncollapseCommitMessage;
    window.isCommitMsgCollapsed = isCommitMsgCollapsed;
    window.onPgCommitMsgBarClick = onPgCommitMsgBarClick;
    window.jumpToPgMergeList = jumpToPgMergeList;
    window.isPgMergeCommit = isPgMergeCommit;
    window.getPgMergeList = getPgMergeList;
    window.renderMergeListPanelHtml = renderMergeListPanelHtml;
    window.scrollToMergeListBox = scrollToMergeListBox;
    window.toggleMergeList = toggleMergeList;
    window.uncollapseMergeList = uncollapseMergeList;
    window.isMergeListCollapsed = isMergeListCollapsed;
    window.onPgMergeListBarClick = onPgMergeListBarClick;
    window.copyMergeListShas = copyMergeListShas;
    window.selectFullMergeList = selectFullMergeList;
    window.navigatePgFileDiff = navigatePgFileDiff;
    window.getPgDiffChunks = getPgDiffChunks;
    window.navigatePgDiffChunk = navigatePgDiffChunk;
    window.renderPgPatchsetSelectorHtml = renderPgPatchsetSelectorHtml;
    window.renderPgPatchRangePopoverHtml = renderPgPatchRangePopoverHtml;
    window.renderPgInterdiffBannerHtml = renderPgInterdiffBannerHtml;
    window.togglePgPatchRangeMenu = togglePgPatchRangeMenu;
    window.closePgPatchRangeMenu = closePgPatchRangeMenu;
    window.togglePgDiffPreviousPatchset = togglePgDiffPreviousPatchset;
    window.onPgPatchsetComparisonChanged = onPgPatchsetComparisonChanged;
    window.selectPgPatchsetComparison = selectPgPatchsetComparison;
    window.stepPgPatchset = stepPgPatchset;
    window.resetPgPatchsetComparisonToBase = resetPgPatchsetComparisonToBase;
    window.getStoredPgDiffViewMode = getStoredPgDiffViewMode;
    window.getPgFileTreeMode = getPgFileTreeMode;
    window.setPgFileTreeMode = setPgFileTreeMode;
    window.togglePgFileTreeMode = togglePgFileTreeMode;
    window.getPgCollapsedDirs = getPgCollapsedDirs;
    window.savePgCollapsedDirs = savePgCollapsedDirs;
    window.copyPgDirPath = copyPgDirPath;
    window.onPgDirRowKeyDown = onPgDirRowKeyDown;
    window.togglePgDirCollapse = togglePgDirCollapse;
    window.areAllPgDirsCollapsed = areAllPgDirsCollapsed;
    window.toggleAllPgDirsCollapse = toggleAllPgDirsCollapse;
    window.syncPgToggleAllDirsButton = syncPgToggleAllDirsButton;
    window.getPgDirRowEl = getPgDirRowEl;
    window.getPgChildFileRows = getPgChildFileRows;
    window.getPgDiffViewMode = getPgDiffViewMode;
    window.setPgDiffViewMode = setPgDiffViewMode;
    window.togglePgDiffViewMode = togglePgDiffViewMode;
    window.togglePgFileDiffMode = togglePgFileDiffMode;
    window.renderPgDiffModeSelectorHtml = renderPgDiffModeSelectorHtml;
    window.DIFF_PREFS_STORAGE_KEY = DIFF_PREFS_STORAGE_KEY;
    window.DEFAULT_DIFF_PREFERENCES = DEFAULT_DIFF_PREFERENCES;
    window.loadPgDiffPreferences = loadPgDiffPreferences;
    window.savePgDiffPreferences = savePgDiffPreferences;
    window.getPgDiffPreferences = getPgDiffPreferences;
    window.setPgDiffPreferences = setPgDiffPreferences;
    window.applyPgDiffPreferences = applyPgDiffPreferences;
    window.openPgDiffPreferencesDialog = openPgDiffPreferencesDialog;
    window.closePgDiffPreferencesDialog = closePgDiffPreferencesDialog;
    window.togglePgDiffPreferencesDialog = togglePgDiffPreferencesDialog;
    window.saveAndApplyPgDiffPreferences = saveAndApplyPgDiffPreferences;
    window.resetPgDiffPreferencesToDefaults = resetPgDiffPreferencesToDefaults;
    window.syncPgDiffContextChips = syncPgDiffContextChips;
    window.applyPgDiffContextPreset = applyPgDiffContextPreset;
    window.onPgDiffPrefContextChange = onPgDiffPrefContextChange;
    window.syncPgDiffIgnoreWsChips = syncPgDiffIgnoreWsChips;
    window.applyPgDiffIgnoreWsPreset = applyPgDiffIgnoreWsPreset;
    window.onPgDiffPrefIgnoreWhitespaceChange = onPgDiffPrefIgnoreWhitespaceChange;
    window.updatePgThemePreview = updatePgThemePreview;
    window.renderPgDiffPreferencesButtonHtml = renderPgDiffPreferencesButtonHtml;
    window.renderPgDiffWrapButtonHtml = renderPgDiffWrapButtonHtml;
    window.renderPgDiffLineNumbersButtonHtml = renderPgDiffLineNumbersButtonHtml;
    window.renderPgDiffIntralineButtonHtml = renderPgDiffIntralineButtonHtml;
    window.renderPgDiffLeftPaneToggleButtonHtml = renderPgDiffLeftPaneToggleButtonHtml;
    window.togglePgLeftDiffPane = togglePgLeftDiffPane;
    window.renderPgDiffOverviewToggleButtonHtml = renderPgDiffOverviewToggleButtonHtml;
    window.togglePgDiffOverviewBar = togglePgDiffOverviewBar;
    window.renderPgDiffOverviewBarHtml = renderPgDiffOverviewBarHtml;
    window.buildPgDiffOverviewMarkers = buildPgDiffOverviewMarkers;
    window.jumpPgDiffOverviewRatio = jumpPgDiffOverviewRatio;
    window.syncPgDiffOverviewThumbs = syncPgDiffOverviewThumbs;
    window.onPgDiffOverviewTrackClick = onPgDiffOverviewTrackClick;
    window.onPgDiffOverviewTrackMouseDown = onPgDiffOverviewTrackMouseDown;
    window.renderPgDiffGoToLineButtonHtml = renderPgDiffGoToLineButtonHtml;
    window.openPgGoToLineModal = openPgGoToLineModal;
    window.closePgGoToLineModal = closePgGoToLineModal;
    window.executePgGoToLine = executePgGoToLine;
    window.DIFF_CONTEXT_OPTIONS = DIFF_CONTEXT_OPTIONS;
    window.getPgDiffContext = getPgDiffContext;
    window.setPgDiffContext = setPgDiffContext;
    window.cyclePgDiffContext = cyclePgDiffContext;
    window.stepPgDiffContext = stepPgDiffContext;
    window.renderPgDiffContextSelectHtml = renderPgDiffContextSelectHtml;
    window.renderPgDiffTabSizeSelectHtml = renderPgDiffTabSizeSelectHtml;
    window.renderPgDiffLineHeightSelectHtml = renderPgDiffLineHeightSelectHtml;
    window.renderPgDiffWhitespaceSelectHtml = renderPgDiffWhitespaceSelectHtml;
    window.togglePgDiffWordWrap = togglePgDiffWordWrap;
    window.togglePgDiffLineNumbers = togglePgDiffLineNumbers;
    window.getPgDiffIntraline = getPgDiffIntraline;
    window.setPgDiffIntraline = setPgDiffIntraline;
    window.togglePgDiffIntraline = togglePgDiffIntraline;
    window.setPgDiffTabWidth = setPgDiffTabWidth;
    window.cyclePgDiffTabWidth = cyclePgDiffTabWidth;
    window.DIFF_TAB_WIDTH_OPTIONS = DIFF_TAB_WIDTH_OPTIONS;
    window.syncPgDiffTabWidthChips = syncPgDiffTabWidthChips;
    window.applyPgDiffTabWidthPreset = applyPgDiffTabWidthPreset;
    window.onPgDiffPrefTabWidthChange = onPgDiffPrefTabWidthChange;
    window.getPgDiffShowTabs = getPgDiffShowTabs;
    window.setPgDiffShowTabs = setPgDiffShowTabs;
    window.togglePgDiffShowTabs = togglePgDiffShowTabs;
    window.getPgDiffShowWhitespaceErrors = getPgDiffShowWhitespaceErrors;
    window.setPgDiffShowWhitespaceErrors = setPgDiffShowWhitespaceErrors;
    window.togglePgDiffShowWhitespaceErrors = togglePgDiffShowWhitespaceErrors;
    window.getPgDiffAutoMarkViewed = getPgDiffAutoMarkViewed;
    window.setPgDiffAutoMarkViewed = setPgDiffAutoMarkViewed;
    window.togglePgDiffAutoMarkViewed = togglePgDiffAutoMarkViewed;
    window.DIFF_FONT_FAMILIES = DIFF_FONT_FAMILIES;
    window.DIFF_LINE_HEIGHTS = DIFF_LINE_HEIGHTS;
    window.DIFF_FONT_SIZES = DIFF_FONT_SIZES;
    window.DIFF_LINE_LENGTHS = DIFF_LINE_LENGTHS;
    window.resolvePgDiffFontFamily = resolvePgDiffFontFamily;
    window.getPgDiffFontFamily = getPgDiffFontFamily;
    window.setPgDiffFontFamily = setPgDiffFontFamily;
    window.getPgDiffLineHeight = getPgDiffLineHeight;
    window.setPgDiffLineHeight = setPgDiffLineHeight;
    window.getPgDiffFontSize = getPgDiffFontSize;
    window.setPgDiffFontSize = setPgDiffFontSize;
    window.stepPgDiffFontSize = stepPgDiffFontSize;
    window.cyclePgDiffFontSize = cyclePgDiffFontSize;
    window.applyPgDiffFontSizePreset = applyPgDiffFontSizePreset;
    window.onPgDiffPrefFontSizeChange = onPgDiffPrefFontSizeChange;
    window.renderPgDiffFontSizeSelectHtml = renderPgDiffFontSizeSelectHtml;
    window.getPgDiffLineLength = getPgDiffLineLength;
    window.setPgDiffLineLength = setPgDiffLineLength;
    window.cyclePgDiffLineLength = cyclePgDiffLineLength;
    window.applyPgDiffLineLengthPreset = applyPgDiffLineLengthPreset;
    window.onPgDiffPrefLineLengthChange = onPgDiffPrefLineLengthChange;
    window.renderPgDiffLineLengthSelectHtml = renderPgDiffLineLengthSelectHtml;
    window.cyclePgDiffLineHeight = cyclePgDiffLineHeight;
    window.stepPgDiffLineHeight = stepPgDiffLineHeight;
    window.syncPgDiffLineHeightChips = syncPgDiffLineHeightChips;
    window.applyPgDiffLineHeightPreset = applyPgDiffLineHeightPreset;
    window.onPgDiffPrefLineHeightChange = onPgDiffPrefLineHeightChange;
    window.renderPgDiffFontSizeStepperHtml = renderPgDiffFontSizeStepperHtml;
    window.renderPgDiffLineHeightStepperHtml = renderPgDiffLineHeightStepperHtml;
    window.onPgDiffPrefFontFamilyChange = onPgDiffPrefFontFamilyChange;
    window.syncPgDiffFontFamilyChips = syncPgDiffFontFamilyChips;
    window.previewPgDiffFontFamily = previewPgDiffFontFamily;
    window.resetPgDiffFontFamilyPreview = resetPgDiffFontFamilyPreview;
    window.applyPgDiffFontFamilyPreset = applyPgDiffFontFamilyPreset;
    window.syncPgDiffColorSchemeChips = syncPgDiffColorSchemeChips;
    window.previewPgDiffColorScheme = previewPgDiffColorScheme;
    window.resetPgDiffColorSchemePreview = resetPgDiffColorSchemePreview;
    window.applyPgDiffColorSchemePreset = applyPgDiffColorSchemePreset;
    window.setPgDiffIgnoreWhitespace = setPgDiffIgnoreWhitespace;
    window.syncPgDiffToolbarPreferences = syncPgDiffToolbarPreferences;
    window.updatePgThemePreview = updatePgThemePreview;
    window.onPgDiffPrefColorSchemeChange = onPgDiffPrefColorSchemeChange;
    window.DIFF_COLOR_SCHEMES = DIFF_COLOR_SCHEMES;
    window.setDiffColorScheme = setDiffColorScheme;
    window.cycleDiffColorScheme = cycleDiffColorScheme;
    window.renderPgDiffFindButtonHtml = renderPgDiffFindButtonHtml;
    window.renderPgDiffFindBarHtml = renderPgDiffFindBarHtml;
    window.renderPgFileJumpMenuHtml = renderPgFileJumpMenuHtml;
    window.openPgFileJumpMenu = openPgFileJumpMenu;
    window.closePgFileJumpMenu = closePgFileJumpMenu;
    window.togglePgFileJumpMenu = togglePgFileJumpMenu;
    window.selectPgFileJumpItem = selectPgFileJumpItem;
    window.selectPgFileJumpCommitMsg = selectPgFileJumpCommitMsg;
    window.onPgFileJumpItemClick = onPgFileJumpItemClick;
    window.onPgFileJumpFilterInput = onPgFileJumpFilterInput;
    window.clearPgFileJumpFilter = clearPgFileJumpFilter;
    window.onPgFileJumpFilterKeyDown = onPgFileJumpFilterKeyDown;
    window.scrollToPgFileList = scrollToPgFileList;
    window.updatePgActiveFile = updatePgActiveFile;
    window.syncPgActiveFileFromScroll = syncPgActiveFileFromScroll;
    window.syncPgStickyFileHeaders = syncPgStickyFileHeaders;
    window.openPgHeaderReply = openPgHeaderReply;
    window.scrollPgChangeToTop = scrollPgChangeToTop;
    window.openPgDiffFindBar = openPgDiffFindBar;
    window.closePgDiffFindBar = closePgDiffFindBar;
    window.togglePgDiffFindBar = togglePgDiffFindBar;
    window.onPgDiffFindInputChanged = onPgDiffFindInputChanged;
    window.onPgDiffFindInputKeyDown = onPgDiffFindInputKeyDown;
    window.stepPgDiffFindMatch = stepPgDiffFindMatch;
    window.togglePgDiffFindOption = togglePgDiffFindOption;
    window.clearPgDiffFindHighlights = clearPgDiffFindHighlights;
    window.executePgDiffSearch = executePgDiffSearch;
    window.focusPgDiffFindMatch = focusPgDiffFindMatch;
    window.loadPgDiffFindOptions = loadPgDiffFindOptions;
    window.savePgDiffFindOptions = savePgDiffFindOptions;
    window.navigatePgUnresolvedThread = navigatePgUnresolvedThread;
    window.isPgShowResolvedCommentsEnabled = isPgShowResolvedCommentsEnabled;
    window.setPgShowResolvedCommentsEnabled = setPgShowResolvedCommentsEnabled;
    window.togglePgShowResolvedComments = togglePgShowResolvedComments;
    window.areAllPgCommentsCollapsed = areAllPgCommentsCollapsed;
    window.togglePgAllCommentsCollapse = togglePgAllCommentsCollapse;
    window.syncPgAllCommentsCollapseButton = syncPgAllCommentsCollapseButton;
    window.onPgFileCommentsClick = onPgFileCommentsClick;
    window.openPgConfirmDiscardDialog = openPgConfirmDiscardDialog;
    window.closePgConfirmDiscardDialog = closePgConfirmDiscardDialog;
    window.onPgConfirmDiscardBackdropClick = onPgConfirmDiscardBackdropClick;
    window.executePgConfirmDiscard = executePgConfirmDiscard;
    window.promptDiscardReplyDialogDraft = promptDiscardReplyDialogDraft;
    window.promptDiscardInlineDraft = promptDiscardInlineDraft;
    window.promptDiscardAllReplyDialogDrafts = promptDiscardAllReplyDialogDrafts;
    window.discardReplyDialogDraftWithConfirm = discardReplyDialogDraftWithConfirm;
    window.discardInlineDraftWithConfirm = discardInlineDraftWithConfirm;
    window.discardAllReplyDialogDraftsWithConfirm = discardAllReplyDialogDraftsWithConfirm;
    window.discardReplyDialogDraft = discardReplyDialogDraft;
    window.discardInlineDraft = discardInlineDraft;
    window.discardAllReplyDialogDrafts = discardAllReplyDialogDrafts;
    window.editReplyDialogDraft = editReplyDialogDraft;
    window.autoResizePgTextarea = autoResizePgTextarea;
    window.editInlineDraftInPlace = editInlineDraftInPlace;
    window.saveInlineDraftInPlace = saveInlineDraftInPlace;
    window.cancelInlineDraftInPlace = cancelInlineDraftInPlace;
    window.setInlineDraftTab = setInlineDraftTab;
    window.updatePgInlineDraftAutosaveBadge = updatePgInlineDraftAutosaveBadge;
    window.schedulePgInlineDraftAutosave = schedulePgInlineDraftAutosave;
    window.flushPgInlineDraftAutosave = flushPgInlineDraftAutosave;
    window.updatePgReplyDraftAutosaveBadge = updatePgReplyDraftAutosaveBadge;
    window.schedulePgReplyDraftAutosave = schedulePgReplyDraftAutosave;
    window.flushPgReplyDraftAutosave = flushPgReplyDraftAutosave;
    window.updatePgComposerAutosaveBadge = updatePgComposerAutosaveBadge;
    window.schedulePgComposerDraftAutosave = schedulePgComposerDraftAutosave;
    window.flushPgComposerDraftAutosave = flushPgComposerDraftAutosave;
    window.onInlineDraftEditInput = onInlineDraftEditInput;
    window.onInlineDraftResolvedChange = onInlineDraftResolvedChange;
    window.onInlineDraftEditKeydown = onInlineDraftEditKeydown;
    window.saveSelectionDraftFromComposer = saveSelectionDraftFromComposer;
    window.postInlineDraftToGerrit = postInlineDraftToGerrit;
    window.triggerPgAddComment = triggerPgAddComment;
    window.navigatePgDraftComment = navigatePgDraftComment;
    window.renderPgDraftsSummaryChipHtml = renderPgDraftsSummaryChipHtml;
    window.syncPgDraftsSummaryChip = syncPgDraftsSummaryChip;
    window.renderPgChecksTabButtonHtml = renderPgChecksTabButtonHtml;
    window.renderPgChecksControlsHtml = renderPgChecksControlsHtml;
    window.renderPgChecksViewHtml = renderPgChecksViewHtml;
    window.renderPgCheckDetailCardHtml = renderPgCheckDetailCardHtml;
    window.selectPgChangeTab = selectPgChangeTab;
    window.togglePgChangeTab = togglePgChangeTab;
    window.selectPgCheckRunByIndex = selectPgCheckRunByIndex;
    window.setPgChecksCategoryFilter = setPgChecksCategoryFilter;
    window.setPgChecksKindFilter = setPgChecksKindFilter;
    window.renderPgCheckRunLogDrawerHtml = renderPgCheckRunLogDrawerHtml;
    window.generatePgCheckRunLogLines = generatePgCheckRunLogLines;
    window.togglePgCheckLogDrawer = togglePgCheckLogDrawer;
    window.onPgCheckLogSearchInput = onPgCheckLogSearchInput;
    window.clearPgCheckLogSearch = clearPgCheckLogSearch;
    window.copyPgCheckLog = copyPgCheckLog;
    window.downloadPgCheckLog = downloadPgCheckLog;
    window.openPgCheckBuganizer = openPgCheckBuganizer;
    window.copyPgCheckDiagnostics = copyPgCheckDiagnostics;
    window.navigatePgCheckRun = navigatePgCheckRun;
    window.onPgChecksSearchInput = onPgChecksSearchInput;
    window.clearPgChecksSearch = clearPgChecksSearch;
    window.rerenderPgChecksViewDom = rerenderPgChecksViewDom;
    window.openPgChecksRerunModal = openPgChecksRerunModal;
    window.closePgChecksRerunModal = closePgChecksRerunModal;
    window.onPgChecksRerunBackdropClick = onPgChecksRerunBackdropClick;
    window.setPgChecksRerunMode = setPgChecksRerunMode;
    window.togglePgChecksRerunTarget = togglePgChecksRerunTarget;
    window.selectAllPgChecksRerunTargets = selectAllPgChecksRerunTargets;
    window.onPgChecksRerunFilterInput = onPgChecksRerunFilterInput;
    window.renderPgChecksRerunModalDom = renderPgChecksRerunModalDom;
    window.confirmPgChecksRerun = confirmPgChecksRerun;
    window.triggerPgSingleCheckRerun = triggerPgSingleCheckRerun;
    window.applyPgShowResolvedCommentsToDom = applyPgShowResolvedCommentsToDom;
    window.isPgRobotComment = isPgRobotComment;
    window.isPgShowRobotCommentsEnabled = isPgShowRobotCommentsEnabled;
    window.setPgShowRobotCommentsEnabled = setPgShowRobotCommentsEnabled;
    window.togglePgShowRobotComments = togglePgShowRobotComments;
    window.applyPgShowRobotCommentsToDom = applyPgShowRobotCommentsToDom;
    window.syncPgRobotCommentsToggleBtn = syncPgRobotCommentsToggleBtn;
    window.countPgRobotComments = countPgRobotComments;
    window.renderPgRobotCommentsChipHtml = renderPgRobotCommentsChipHtml;
    window.navigatePgRobotComment = navigatePgRobotComment;
    window.onPgFileRobotCommentsClick = onPgFileRobotCommentsClick;
    window.renderPgFixSuggestionsHtml = renderPgFixSuggestionsHtml;
    window.copyPgFixCode = copyPgFixCode;
    window.applyPgFixToReply = applyPgFixToReply;
    window.onPgDiffPreferencesBackdropClick = onPgDiffPreferencesBackdropClick;
    window.isWhitespaceDiffOnly = isWhitespaceDiffOnly;
    window.renderFileDiffSideBySideHtml = renderFileDiffSideBySideHtml;
    window.renderFileDiffUnifiedHtml = renderFileDiffUnifiedHtml;
    window.renderFileDiffBlock = renderFileDiffBlock;
    window.extractPgFileDiffSymbols = extractPgFileDiffSymbols;
    window.renderPgFileOutlineButtonHtml = renderPgFileOutlineButtonHtml;
    window.renderPgFileOutlineDropdownHtml = renderPgFileOutlineDropdownHtml;
    window.togglePgFileOutlineMenu = togglePgFileOutlineMenu;
    window.closeAllPgFileOutlineMenus = closeAllPgFileOutlineMenus;
    window.onPgFileOutlineFilterInput = onPgFileOutlineFilterInput;
    window.jumpToPgFileSymbol = jumpToPgFileSymbol;
    window.splitDiffSubtokens = splitDiffSubtokens;
    window.refineIntralineTokenPair = refineIntralineTokenPair;
    window.computePairIntralineIntervals = computePairIntralineIntervals;
    window.computeFileIntralineRanges = computeFileIntralineRanges;
    window.copyPgFilePath = copyPgFilePath;
    window.copyPgFilePaths = copyPgFilePaths;
    window.closePgCopyFilePathsMenu = closePgCopyFilePathsMenu;
    window.togglePgCopyFilePathsMenu = togglePgCopyFilePathsMenu;
    window.renderPgCopyFilePathsDropdownHtml = renderPgCopyFilePathsDropdownHtml;
    window.getPgFileOldPath = getPgFileOldPath;
    window.findPgStackForClOrId = findPgStackForClOrId;
    window.findChangeByRevision = findChangeByRevision;
    window.isContinuousStackDiffActive = isContinuousStackDiffActive;
    window.toggleContinuousStackDiff = toggleContinuousStackDiff;
    window.scrollToStackCommit = scrollToStackCommit;
    window.scrollDiffToTop = scrollDiffToTop;
    window.selectStackForBatchEdit = selectStackForBatchEdit;
    window.canPgStackInterdiff = canPgStackInterdiff;
    window.isPgStackInterdiffActive = isPgStackInterdiffActive;
    window.togglePgStackInterdiff = togglePgStackInterdiff;
    window.PG_PATCHSET_COMPARISON_STORAGE_PREFIX = PG_PATCHSET_COMPARISON_STORAGE_PREFIX;
    window.PG_STACK_INTERDIFF_STORAGE_PREFIX = PG_STACK_INTERDIFF_STORAGE_PREFIX;
    window.getPgPersistedPatchsetComparison = getPgPersistedPatchsetComparison;
    window.setPgPersistedPatchsetComparison = setPgPersistedPatchsetComparison;
    window.clearPgPersistedPatchsetComparison = clearPgPersistedPatchsetComparison;
    window.getPgSelectedPatchset = getPgSelectedPatchset;
    window.getPgPersistedStackInterdiff = getPgPersistedStackInterdiff;
    window.setPgPersistedStackInterdiff = setPgPersistedStackInterdiff;
    window.ensurePgStackInterdiffRestored = ensurePgStackInterdiffRestored;
    window.renderPgContinuousStackBannerHtml = renderPgContinuousStackBannerHtml;
    window.renderPgStackCommitSequenceBarHtml = renderPgStackCommitSequenceBarHtml;
    window.renderPgRelationChainHtml = renderPgRelationChainHtml;
    window.renderPgRelBaseRowHtml = renderPgRelBaseRowHtml;
    window.renderPgRelStatusTag = renderPgRelStatusTag;
    window.filterDashboardByBranch = filterDashboardByBranch;
    window.copyPgBranch = copyPgBranch;
    window.copyPgBaseCommitSha = copyPgBaseCommitSha;
    window.onPgRelationBaseItemKeyDown = onPgRelationBaseItemKeyDown;
    window.getPgBranchDisplay = getPgBranchDisplay;
    window.getPgProjectDisplay = getPgProjectDisplay;
    window.getPgTopicPeers = getPgTopicPeers;
    window.getPgSubmittedTogetherPeers = getPgSubmittedTogetherPeers;
    window.getPgChangeId = getPgChangeId;
    window.getPgCherryPickPeers = getPgCherryPickPeers;
    window.getPgCherryPickGroup = getPgCherryPickGroup;
    window.copyPgChangeId = copyPgChangeId;
    window.applyPgChangeIdFilter = applyPgChangeIdFilter;
    window.applyPgSubmittedTogetherFilter = applyPgSubmittedTogetherFilter;
    window.onPgRelationItemKeyDown = onPgRelationItemKeyDown;
    window.focusPgRelationItem = focusPgRelationItem;
    window.getPgRelatedChangesTab = getPgRelatedChangesTab;
    window.setPgRelatedChangesTab = setPgRelatedChangesTab;
    window.togglePgRelatedChangesTab = togglePgRelatedChangesTab;
    window.state = state;
    window.applyPgTopicFilter = applyPgTopicFilter;
    window.copyPgTopic = copyPgTopic;
    window.showPgEditTopicInput = showPgEditTopicInput;
    window.hidePgEditTopicInput = hidePgEditTopicInput;
    window.submitPgEditTopic = submitPgEditTopic;
    window.removePgTopic = removePgTopic;
    window.onPgEditTopicKeyDown = onPgEditTopicKeyDown;
    window.renderCommitReviewersPanelHtml = renderCommitReviewersPanelHtml;
    window.renderPgDashboardRowHtml = renderPgDashboardRowHtml;
    window.renderPgChangeHeaderHtml = renderPgChangeHeaderHtml;
    window.renderCommitHarnessActionsHtml = renderCommitHarnessActionsHtml;
    window.renderCommitNavButtonsHtml = renderCommitNavButtonsHtml;
    window.navigateVisibleCl = navigateVisibleCl;
    window.syncToolbarNavButtons = syncToolbarNavButtons;
    window.renderCommitAemuMapButtonHtml = renderCommitAemuMapButtonHtml;
    window.buildAemuMapCommitUrl = buildAemuMapCommitUrl;
    window.syncAemuMapButtons = syncAemuMapButtons;
    window.filterDashboardByHashtag = filterDashboardByHashtag;
    window.applyPgHashtagFilter = applyPgHashtagFilter;
    window.copyPgHashtag = copyPgHashtag;
    window.addPgHashtag = addPgHashtag;
    window.removePgHashtag = removePgHashtag;
    window.showPgAddHashtagInput = showPgAddHashtagInput;
    window.hidePgAddHashtagInput = hidePgAddHashtagInput;
    window.submitPgAddHashtag = submitPgAddHashtag;
    window.onPgAddHashtagKeyDown = onPgAddHashtagKeyDown;
    window.copyPgRelatedCls = copyPgRelatedCls;
    window.renderPgRelVoteChip = renderPgRelVoteChip;
    window.renderPgRelStatusTag = renderPgRelStatusTag;
    window.isClConflict = isClConflict;
    window.getPgChangeRebaseStatus = getPgChangeRebaseStatus;
    window.fetchPgMergeable = fetchPgMergeable;
    window.loadPgMergeable = loadPgMergeable;
    window.syncPgClMergeability = syncPgClMergeability;
    window.refreshPgMergeableStatus = refreshPgMergeableStatus;
    window.renderPgRebaseNoticeBannerHtml = renderPgRebaseNoticeBannerHtml;
    window.onPgRelationRebaseClick = onPgRelationRebaseClick;
    window.quickRebaseOnParent = quickRebaseOnParent;
    window.quickRebaseStackChain = quickRebaseStackChain;
    window.isPgStackCommitReviewed = isPgStackCommitReviewed;
    window.togglePgStackCommitReviewed = togglePgStackCommitReviewed;
    window.markAllStackCommitsReviewed = markAllStackCommitsReviewed;
    window.resetStackCommitsReviewed = resetStackCommitsReviewed;
    window.togglePgStackCommitCollapse = togglePgStackCommitCollapse;
    window.toggleAllStackCommitsCollapse = toggleAllStackCommitsCollapse;
    window.goToNextStackCommit = goToNextStackCommit;
    window.goToPrevStackCommit = goToPrevStackCommit;
    window.approveStackForBatchEdit = approveStackForBatchEdit;
    window.syncPgStackUiState = syncPgStackUiState;
    window.getPgStackSubmitSequence = getPgStackSubmitSequence;
    window.renderPgStackSequenceDialogBodyDom = renderPgStackSequenceDialogBodyDom;
    window.openPgStackSequenceDialog = openPgStackSequenceDialog;
    window.closePgStackSequenceDialog = closePgStackSequenceDialog;
    window.togglePgStackSequenceDialog = togglePgStackSequenceDialog;
    window.isPgStackSequenceDialogOpen = isPgStackSequenceDialogOpen;
    window.copyPgStackSequenceReport = copyPgStackSequenceReport;
    window.getPgCumulativeStackFiles = getPgCumulativeStackFiles;
    window.renderPgStackFilesDialogBodyDom = renderPgStackFilesDialogBodyDom;
    window.openPgStackFilesDrawer = openPgStackFilesDrawer;
    window.closePgStackFilesDrawer = closePgStackFilesDrawer;
    window.togglePgStackFilesDrawer = togglePgStackFilesDrawer;
    window.isPgStackFilesDrawerOpen = isPgStackFilesDrawerOpen;
    window.jumpToStackFileDiff = jumpToStackFileDiff;
    window.copyPgStackFilesList = copyPgStackFilesList;
    window.onPgStackFilesSearchInput = onPgStackFilesSearchInput;
    window.clearPgStackFilesSearch = clearPgStackFilesSearch;
    window.setPgStackFilesFilterExt = setPgStackFilesFilterExt;
    window.syncPgRelationSlots = syncPgRelationSlots;
    window.getPgFileExtension = getPgFileExtension;
    window.isPgImageFile = isPgImageFile;
    window.isPgBinaryFile = isPgBinaryFile;
    window.getPgImageFormat = getPgImageFormat;
    window.renderPgImageViewerHtml = renderPgImageViewerHtml;
    window.renderPgBinaryFileHtml = renderPgBinaryFileHtml;
    window.setPgImageDiffMode = setPgImageDiffMode;
    window.setPgImageZoom = setPgImageZoom;
    window.setPgImageBackground = setPgImageBackground;
    window.cyclePgImageBackground = cyclePgImageBackground;
    window.isPgImageSyncPanEnabled = isPgImageSyncPanEnabled;
    window.togglePgImageSyncPan = togglePgImageSyncPan;
    window.centerPgImagePan = centerPgImagePan;
    window.cyclePgImageZoom = cyclePgImageZoom;
    window.updatePgImageCrosshair = updatePgImageCrosshair;
    window.onPgImageMouseMove = onPgImageMouseMove;
    window.onPgImageMouseLeave = onPgImageMouseLeave;
    window.onPgImageFrameScroll = onPgImageFrameScroll;
    window.onPgImagePanMouseDown = onPgImagePanMouseDown;
    window.onPgImageDocMouseMove = onPgImageDocMouseMove;
    window.onPgImageDocMouseUp = onPgImageDocMouseUp;
    window.initImagePanListeners = initImagePanListeners;
    window.onPgImageSwipeInput = onPgImageSwipeInput;
    window.onPgImageOnionInput = onPgImageOnionInput;
    window.onPgImageSwipePointerDown = onPgImageSwipePointerDown;
    window.onPgImageSwipePointerMove = onPgImageSwipePointerMove;
    window.onPgImageSwipePointerUp = onPgImageSwipePointerUp;
    window.onPgImageOnionPointerDown = onPgImageOnionPointerDown;
    window.onPgImageOnionPointerMove = onPgImageOnionPointerMove;
    window.onPgImageOnionPointerUp = onPgImageOnionPointerUp;
    window.togglePgImageBlink = togglePgImageBlink;
    window.stopPgImageBlink = stopPgImageBlink;
    window.flipPgImageSides = flipPgImageSides;
    window.onPgImageLoaded = onPgImageLoaded;
    window.onPgImageError = onPgImageError;
    window.cyclePgImageDiffMode = cyclePgImageDiffMode;
    window.loadPgImageData = loadPgImageData;
    window.reloadPgImageData = reloadPgImageData;
    window.downloadPgBinaryFile = downloadPgBinaryFile;
    window.toggleStagedVerified = toggleStagedVerified;
    window.V_STAGE_BUTTON_IDS = V_STAGE_BUTTON_IDS;
    window.PG_V_SCORE_DESCRIPTIONS = PG_V_SCORE_DESCRIPTIONS;
    window.formatVoteLabelsText = formatVoteLabelsText;
    window.PG_REPLY_QUICK_PRESETS = PG_REPLY_QUICK_PRESETS;
    window.renderPgReplyQuickChipsHtml = renderPgReplyQuickChipsHtml;
    window.applyPgReplyQuickPreset = applyPgReplyQuickPreset;
    window.syncPgReplyQuickChipsActiveState = syncPgReplyQuickChipsActiveState;
  }

