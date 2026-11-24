import React from 'react';
import { escapeJsQuote, renderReactToHtml } from '../runtime';

export const PRESUBMIT_KIND_LABELS: Record<string, string> = {
  build: 'BUILD',
  test: 'TEST',
  preflight: 'PREFLIGHT',
  test_module: 'MODULE',
};

export interface PresubmitTargetItem {
  name: string;
  kind?: string;
  status?: string;
  raw_status?: string;
  cached?: boolean;
  waiting_on_build?: boolean;
  blocking_level?: string;
  subtitle?: string;
  duration_display?: string;
  artifact_id?: string;
  url?: string;
  secondary_url?: string;
  secondary_label?: string;
  worknode_id?: string;
  branch?: string;
  build_target?: string;
}

export interface PresubmitLogLine {
  type?: string;
  text: string;
}

export function PresubmitStatusPill(props: { t: PresubmitTargetItem }): React.ReactElement {
  const { t } = props;
  const st = t.status || 'queued';
  if (st === 'failed') {
    const txt =
      t.raw_status === 'TIMED_OUT'
        ? '✗ TIMEOUT'
        : t.raw_status === 'INFRA_ERROR'
          ? '✗ INFRA ERR'
          : '✗ FAIL';
    return <span className="ps-status-pill st-failed">{txt}</span>;
  }
  if (st === 'warning') {
    return <span className="ps-status-pill st-warning">⚠ WARN</span>;
  }
  if (st === 'running') {
    return (
      <span className="ps-status-pill st-running">
        <span className="pv-mini-spinner"></span> RUN
      </span>
    );
  }
  if (st === 'queued') {
    return <span className="ps-status-pill st-queued">⏳ QUEUED</span>;
  }
  if (st === 'passed' && t.cached) {
    return <span className="ps-status-pill st-cached">✓ CACHED</span>;
  }
  if (st === 'passed') {
    return <span className="ps-status-pill st-passed">✓ PASS</span>;
  }
  const skipTxt = t.raw_status === 'DEPENDENCY_FAILED' ? '— DEP FAIL' : '— SKIP';
  return <span className="ps-status-pill st-skipped">{skipTxt}</span>;
}

export function SinglePresubmitTargetRow(props: {
  t: PresubmitTargetItem;
  fallbackChecksUrl?: string;
}): React.ReactElement {
  const { t, fallbackChecksUrl } = props;
  const mainUrl = t.url || fallbackChecksUrl || '#';
  const kindLabel = (t.kind && PRESUBMIT_KIND_LABELS[t.kind]) || 'TARGET';
  const isWarnOrNonBlock = t.blocking_level === 'WARN' || t.blocking_level === 'NON_BLOCKING';
  const tipParts = [`[${kindLabel}] ${t.name}`, `Status: ${t.raw_status || t.status}`];
  if (t.subtitle) tipParts.push(t.subtitle);
  if (t.artifact_id) tipParts.push(`ID: ${t.artifact_id}`);
  if (t.duration_display) tipParts.push(`Duration: ${t.duration_display}`);

  return (
    <div className={`ps-target-row status-${t.status || 'queued'}`}>
      <a
        className="ps-target-main-link"
        href={mainUrl}
        target="_blank"
        rel="noopener noreferrer"
        title={tipParts.join(' · ')}
        onClick={'event.stopPropagation()' as unknown as React.MouseEventHandler}
      >
        <PresubmitStatusPill t={t} />
        <span className="ps-kind-tag">{kindLabel}</span>
        <span className="ps-target-name">{`${t.name} ↗`}</span>
        {t.waiting_on_build ? (
          <span
            className="ps-meta-tag"
            title="Waiting for prerequisite build target to finish"
          >
            ⏳ waiting on build
          </span>
        ) : isWarnOrNonBlock ? (
          <span className="ps-meta-tag">
            {t.blocking_level === 'WARN' ? 'warn-only' : 'non-blocking'}
          </span>
        ) : null}
        {t.subtitle && t.subtitle !== t.name ? (
          <span className="ps-target-sub">{`· ${t.subtitle}`}</span>
        ) : null}
      </a>
      <div className="ps-target-right">
        {t.duration_display ? <span>{t.duration_display}</span> : null}
        {t.artifact_id ? (
          <span className="ps-artifact-tag" title="Android Build / AnTS Invocation ID">
            {t.artifact_id}
          </span>
        ) : null}
        {t.secondary_url ? (
          <a
            className="ps-target-sec-link"
            href={t.secondary_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={'event.stopPropagation()' as unknown as React.MouseEventHandler}
          >
            {`${t.secondary_label || 'Details'} ↗`}
          </a>
        ) : null}
      </div>
    </div>
  );
}

export interface ChecksTabButtonProps {
  num: number;
  isActive: boolean;
  badgeClass?: string;
  badgeText?: string;
}

export function ChecksTabButton(props: ChecksTabButtonProps): React.ReactElement {
  const { num, isActive, badgeClass = '', badgeText = '' } = props;
  return (
    <a
      href="javascript:void(0)"
      id={`pgTabChecks-${num}`}
      className={`pg-tab ${isActive ? 'active' : 'pg-tab-link'}`}
      onClick={`selectPgChangeTab(${num}, 'checks')` as unknown as React.MouseEventHandler}
      title="View TreeHugger & Presubmit Checks (Shortcut: Shift+K)"
    >
      {'Checks '}
      {badgeText ? (
        <span className={`pg-checks-tab-badge ${badgeClass}`}>{badgeText}</span>
      ) : null}
    </a>
  );
}

export interface ChecksControlsProps {
  num: number;
  isRefreshing: boolean;
  showStagePrBtn: boolean;
  workplanUrl?: string;
  workplanId?: string;
  checksUrl?: string;
}

export function ChecksControls(props: ChecksControlsProps): React.ReactElement {
  const { num, isRefreshing, showStagePrBtn, workplanUrl, workplanId, checksUrl } = props;
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
      {showStagePrBtn ? (
        <button
          type="button"
          className="pg-btn-subtle"
          onClick={'event.stopPropagation(); toggleStagedPr(true)' as unknown as React.MouseEventHandler}
          title="Stage Presubmit-Ready +1 (Shortcut: Shift+P)"
        >
          ☑ Stage PR +1
        </button>
      ) : null}
      {workplanUrl ? (
        <a
          className="ag-link"
          style={{ fontSize: '12px' }}
          href={workplanUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={'event.stopPropagation()' as unknown as React.MouseEventHandler}
        >
          {`Workplan ${workplanId || ''} ↗`}
        </a>
      ) : null}
      {checksUrl ? (
        <a
          className="ag-link"
          style={{ fontSize: '12px' }}
          href={checksUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={'event.stopPropagation()' as unknown as React.MouseEventHandler}
        >
          Gerrit Checks ↗
        </a>
      ) : null}
      <button
        type="button"
        className="pg-btn-subtle"
        disabled={isRefreshing}
        onClick={`fetchPresubmitTargetsForCl(${num}, { forceRefresh: true })` as unknown as React.MouseEventHandler}
        title="Refresh TreeHugger presubmit targets"
      >
        {isRefreshing ? (
          <>
            <span className="btn-spinner"></span> Syncing…
          </>
        ) : (
          '↻ Refresh'
        )}
      </button>
    </div>
  );
}

export interface CheckRunLogDrawerProps {
  num: number;
  target: PresubmitTargetItem;
  isLogOpen: boolean;
  rawLogSearch: string;
  visibleLines: PresubmitLogLine[];
}

export function CheckRunLogDrawer(props: CheckRunLogDrawerProps): React.ReactElement {
  const { num, target, isLogOpen, rawLogSearch, visibleLines } = props;
  const safeTargetName = escapeJsQuote(target.name || '');
  const kindLabel = (target.kind && PRESUBMIT_KIND_LABELS[target.kind]) || 'TARGET';
  const statusText = target.raw_status || target.status || 'UNKNOWN';
  const logSearchLower = String(rawLogSearch || '').trim().toLowerCase();

  return (
    <div
      className={`pg-checks-log-drawer ${isLogOpen ? 'is-open' : ''}`}
      id={`pgChecksLogDrawer-${num}`}
      style={isLogOpen ? undefined : { display: 'none' }}
    >
      <div className="pg-checks-log-header">
        <div className="pg-checks-log-title-wrap">
          <span className="pg-checks-log-icon">📜</span>
          <span className="pg-checks-log-title">
            {'Console Run Log: '}
            <code>{target.name}</code>
          </span>
          <span className="pg-checks-kind-badge">{kindLabel}</span>
          <span className={`pg-checks-log-status-tag ${target.status || ''}`}>
            {statusText}
          </span>
        </div>
        <div className="pg-checks-log-actions">
          <div className="pg-checks-log-search-wrap">
            <span className="pg-checks-log-search-icon">🔍</span>
            <input
              type="text"
              id={`pgCheckLogFilterInput-${num}`}
              className="pg-checks-log-search-input"
              value={rawLogSearch || ''}
              placeholder="Filter log lines..."
              onInput={`onPgCheckLogSearchInput(${num}, this.value)` as unknown as React.FormEventHandler}
              aria-label="Filter log lines"
            />
            {logSearchLower ? (
              <button
                type="button"
                className="pg-checks-log-search-clear"
                onClick={`clearPgCheckLogSearch(${num})` as unknown as React.MouseEventHandler}
                title="Clear filter"
              >
                ✕
              </button>
            ) : null}
          </div>
          <button
            type="button"
            className="pg-btn-subtle"
            onClick={`copyPgCheckLog(${num}, '${safeTargetName}')` as unknown as React.MouseEventHandler}
            title="Copy full console log"
          >
            📋 Copy
          </button>
          <button
            type="button"
            className="pg-btn-subtle"
            onClick={`downloadPgCheckLog(${num}, '${safeTargetName}')` as unknown as React.MouseEventHandler}
            title="Download log as .log file"
          >
            ⬇ Download
          </button>
          <button
            type="button"
            className="pg-btn-subtle"
            onClick={`togglePgCheckLogDrawer(${num})` as unknown as React.MouseEventHandler}
            title="Close log drawer"
          >
            ✕
          </button>
        </div>
      </div>
      <div className="pg-checks-log-body" id={`pgChecksLogBody-${num}`}>
        <div className="pg-checks-log-terminal">
          {visibleLines.length === 0 ? (
            <div className="pg-checks-log-empty">
              {`No log lines matching "${logSearchLower}". `}
              <button
                type="button"
                className="pg-btn-subtle"
                style={{ marginLeft: '6px' }}
                onClick={`clearPgCheckLogSearch(${num})` as unknown as React.MouseEventHandler}
              >
                Clear Filter
              </button>
            </div>
          ) : (
            visibleLines.map((l, idx) => (
              <div key={idx} className="pg-log-line">
                <span className="pg-log-line-num">{idx + 1}</span>
                <span className={`pg-log-line-text ${l.type ? `pg-log-${l.type}` : ''}`}>
                  {l.text}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export interface CheckDetailCardProps {
  num: number;
  ps: number;
  target: PresubmitTargetItem;
  checksUrl?: string;
  isLogOpen: boolean;
  logDrawerHtml: string;
}

export function CheckDetailCard(props: CheckDetailCardProps): React.ReactElement {
  const { num, ps, target: t, checksUrl, isLogOpen, logDrawerHtml } = props;
  const kindLabel = (t.kind && PRESUBMIT_KIND_LABELS[t.kind]) || 'TARGET';
  const mainUrl = t.url || checksUrl || '#';
  const statusText = t.raw_status || t.status || 'UNKNOWN';
  const durText = t.duration_display || '—';
  const artifactId = t.artifact_id || '';
  const isFailed = t.status === 'failed' || t.status === 'warning';
  const isRunning = t.status === 'running' || t.status === 'queued';
  const isPassed = t.status === 'passed';
  const safeTargetName = escapeJsQuote(t.name || '');

  return (
    <div className="pg-checks-detail-card">
      <div className="pg-checks-detail-head">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <PresubmitStatusPill t={t} />
            <span className="pg-checks-kind-badge">{kindLabel}</span>
            <span className="pg-checks-detail-title">{t.name}</span>
          </div>
          {t.subtitle && t.subtitle !== t.name ? (
            <span style={{ fontSize: '12px', color: 'var(--g-muted)' }}>{t.subtitle}</span>
          ) : null}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <a
            className="pg-btn-primary"
            href={mainUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Inspect on Sponge ↗
          </a>
          {t.secondary_url ? (
            <a
              className="pg-btn-subtle"
              href={t.secondary_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              View Build Logs ↗
            </a>
          ) : null}
          <button
            type="button"
            className="pg-btn-check-rerun"
            id={`btnCheckRerun-${num}`}
            onClick={`openPgChecksRerunModal(${num}, { target: '${safeTargetName}' })` as unknown as React.MouseEventHandler}
            title={`Rerun this check run (${t.name}) (Shortcut: r)`}
          >
            <span>↻</span> <span>Rerun</span>
          </button>
          {isFailed ? (
            <button
              type="button"
              className="pg-btn-buganizer"
              id={`btnCheckBuganizer-${num}`}
              onClick={`openPgCheckBuganizer(${num}, '${safeTargetName}')` as unknown as React.MouseEventHandler}
              title="File a new Buganizer issue for this check failure (b/new)"
            >
              <span>🐛</span> <span>File Bug</span>
            </button>
          ) : null}
          <button
            type="button"
            className="pg-btn-subtle"
            id={`btnCheckCopyDiag-${num}`}
            onClick={`copyPgCheckDiagnostics(${num}, '${safeTargetName}')` as unknown as React.MouseEventHandler}
            title="Copy failure diagnostic report formatted in Markdown"
          >
            <span>📋</span> <span>Diagnostics</span>
          </button>
          <button
            type="button"
            className={`pg-btn-subtle ${isLogOpen ? 'active' : ''}`}
            id={`btnToggleCheckLog-${num}`}
            onClick={`togglePgCheckLogDrawer(${num}, '${safeTargetName}')` as unknown as React.MouseEventHandler}
            title="Toggle console run log drawer (l)"
          >
            <span>📜</span> <span>{isLogOpen ? 'Hide Log' : 'Console Log'}</span>
          </button>
        </div>
      </div>

      {isFailed ? (
        <div className="pg-checks-callout callout-failed">
          <div style={{ fontWeight: 600, fontSize: '12.5px', color: '#f28b82' }}>
            ✕ Failure Diagnostics
          </div>
          <div
            style={{
              fontSize: '12px',
              color: 'var(--g-fg)',
              marginTop: '4px',
              wordBreak: 'break-word',
            }}
          >
            {t.subtitle || 'Target exited with non-zero status or verification failure.'}
          </div>
        </div>
      ) : isRunning ? (
        <div className="pg-checks-callout callout-running">
          <div style={{ fontWeight: 600, fontSize: '12.5px', color: '#8ab4f8' }}>
            ◷ In Progress
          </div>
          <div style={{ fontSize: '12px', color: 'var(--g-fg)', marginTop: '4px' }}>
            {t.waiting_on_build
              ? 'Waiting on prerequisite build target to finish.'
              : 'Job is currently executing on Android Build worker.'}
          </div>
        </div>
      ) : isPassed ? (
        <div className="pg-checks-callout callout-passed">
          <div style={{ fontWeight: 600, fontSize: '12.5px', color: '#81c995' }}>
            ✓ Check Passed
          </div>
          <div style={{ fontSize: '12px', color: 'var(--g-fg)', marginTop: '4px' }}>
            {t.cached
              ? 'Target result verified from Android Build cache.'
              : 'Target executed and verified successfully.'}
          </div>
        </div>
      ) : null}

      <div className="pg-checks-props-table">
        <div className="pg-checks-prop-row">
          <span className="pg-checks-prop-key">Target Name</span>
          <span
            className="pg-checks-prop-val"
            style={{ fontFamily: 'var(--g-mono)', fontSize: '12px' }}
          >
            {t.name}
          </span>
        </div>
        <div className="pg-checks-prop-row">
          <span className="pg-checks-prop-key">Kind</span>
          <span className="pg-checks-prop-val">{kindLabel}</span>
        </div>
        <div className="pg-checks-prop-row">
          <span className="pg-checks-prop-key">Status</span>
          <span className="pg-checks-prop-val">{statusText}</span>
        </div>
        <div className="pg-checks-prop-row">
          <span className="pg-checks-prop-key">Duration</span>
          <span className="pg-checks-prop-val">{durText}</span>
        </div>
        {artifactId ? (
          <div className="pg-checks-prop-row">
            <span className="pg-checks-prop-key">Invocation ID</span>
            <span className="pg-checks-prop-val">
              <code style={{ fontFamily: 'var(--g-mono)', fontSize: '11.5px' }}>
                {artifactId}
              </code>
            </span>
          </div>
        ) : null}
        <div className="pg-checks-prop-row">
          <span className="pg-checks-prop-key">Blocking Level</span>
          <span className="pg-checks-prop-val">{t.blocking_level || 'BLOCKING'}</span>
        </div>
        <div className="pg-checks-prop-row">
          <span className="pg-checks-prop-key">Patchset</span>
          <span className="pg-checks-prop-val">{`PS ${ps}`}</span>
        </div>
      </div>

      <div dangerouslySetInnerHTML={{ __html: logDrawerHtml }} />
    </div>
  );
}

export interface ReplyDialogProps {
  num: number;
  patchSet: number;
  viewer: string;
  replyDialogOpen: boolean;
  policyNote: string;
  initialComment: string;
  hasReplyDraft: boolean;
  participantsHtml: string;
  quickChipsHtml: string;
  markdownToolbarHtml: string;
  attentionChipsHtml: string;
  labelScoresTableHtml: string;
}

export function ReplyDialog(props: ReplyDialogProps): React.ReactElement {
  const {
    num,
    patchSet,
    viewer,
    replyDialogOpen,
    policyNote,
    initialComment,
    hasReplyDraft,
    participantsHtml,
    quickChipsHtml,
    markdownToolbarHtml,
    attentionChipsHtml,
    labelScoresTableHtml,
  } = props;

  return (
    <div
      id="replyDialogBackdrop"
      className={`pg-reply-backdrop${replyDialogOpen ? ' open' : ''}`}
      aria-hidden={replyDialogOpen ? 'false' : 'true'}
      onClick={'cancelReplyDialog()' as unknown as React.MouseEventHandler}
    >
      <div
        id="stagedVoteBar"
        className="pg-reply-dialog gerrit-vote-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`Reply to ag/${num}`}
        onClick={'event.stopPropagation()' as unknown as React.MouseEventHandler}
      >
        <div className="pg-reply-head">
          <span>
            {'Reply to '}
            <strong>{`ag/${num}`}</strong>
            {` · PS${patchSet}`}
          </span>
          <span className="pg-reply-head-sub">{`Acting as ${viewer || 'viewer'}`}</span>
        </div>
        <div
          className="pg-reply-section pg-reply-participants-section"
          id={`pgReplyParticipantsSection-${num}`}
          dangerouslySetInnerHTML={{ __html: participantsHtml }}
        />
        <div className="pg-reply-section pg-reply-quick-section">
          <div className="pg-reply-quick-header">
            <span className="pg-reply-quick-title">Quick responses</span>
            <span className="pg-reply-quick-hint">Click preset or press Alt+L</span>
          </div>
          <div
            className="pg-reply-quick-chips"
            role="toolbar"
            aria-label="Quick reply presets"
            dangerouslySetInnerHTML={{ __html: quickChipsHtml }}
          />
        </div>
        <div className="pg-reply-section pg-reply-message-section">
          <div className="pg-reply-message-head">
            <div className="pg-reply-message-head-left">
              <span className="pg-reply-message-title">Review Message</span>
              <div
                className="pg-reply-message-tabs"
                role="tablist"
                aria-label="Review message editor mode"
              >
                <button
                  type="button"
                  id="btnReplyTabEdit"
                  className="pg-reply-tab is-active"
                  role="tab"
                  aria-selected="true"
                  onClick={`setPgReplyMessageTab('edit', ${num})` as unknown as React.MouseEventHandler}
                  title="Edit review message (Shortcut: Alt+P)"
                >
                  ✎ Edit
                </button>
                <button
                  type="button"
                  id="btnReplyTabPreview"
                  className="pg-reply-tab"
                  role="tab"
                  aria-selected="false"
                  onClick={`setPgReplyMessageTab('preview', ${num})` as unknown as React.MouseEventHandler}
                  title="Preview formatted markdown message (Shortcut: Alt+P)"
                >
                  👁 Preview
                </button>
              </div>
            </div>
            <div className="pg-reply-message-actions">
              <span
                id="replyDialogAutosaveBadge"
                className="pg-draft-autosave-badge is-saved"
                title="Draft saved automatically"
              >
                ✓ Saved
              </span>
              <button
                type="button"
                id="btnDiscardReplyCommentDraft"
                className="pg-btn-subtle pg-btn-discard-reply-draft"
                style={hasReplyDraft ? undefined : { display: 'none' }}
                onClick={`discardReplyCommentDraft(${num})` as unknown as React.MouseEventHandler}
                title="Discard draft review message"
              >
                Discard
              </button>
            </div>
          </div>
          <div dangerouslySetInnerHTML={{ __html: markdownToolbarHtml }} />
          <textarea
            id="stagedVoteCommentInput"
            className="pg-reply-textarea"
            rows={4}
            placeholder="Say something nice… (Ctrl+Enter to send · Esc to close & keep draft · Alt+L for LGTM +2 · Markdown supported)"
            onInput={'onStagedVoteCommentInput(this.value)' as unknown as React.FormEventHandler}
            onBlur={`flushPgReplyDialogDraftAutosave(${num})` as unknown as React.FocusEventHandler}
            onKeyDown={'onStagedVoteCommentKeydown(event)' as unknown as React.KeyboardEventHandler}
            defaultValue={initialComment}
          />
          <div
            id="pgReplyMessagePreview"
            className="pg-reply-message-preview"
            style={{ display: 'none' }}
            aria-label="Review message markdown preview"
          ></div>
        </div>
        <div
          id={`pgReplyDraftsSection-${num}`}
          className="pg-reply-drafts-section"
          style={{ display: 'none' }}
        >
          <div className="pg-reply-drafts-head">
            <div className="pg-reply-drafts-head-left">
              <span className="pg-reply-drafts-title">
                {'Draft Comments ('}
                <span id={`pgReplyDraftsCount-${num}`}>0</span>
                {')'}
              </span>
              <span className="pg-reply-drafts-sub">Will be published to Gerrit on Send</span>
            </div>
            <button
              id={`btnDiscardAllDrafts-${num}`}
              type="button"
              className="pg-reply-draft-discard pg-btn-discard-all-drafts"
              onClick={`discardAllReplyDialogDrafts(${num})` as unknown as React.MouseEventHandler}
              title="Discard all draft comments on this change"
            >
              ✕ Discard all drafts
            </button>
          </div>
          <div id={`pgReplyDraftsList-${num}`} className="pg-reply-drafts-list"></div>
        </div>
        <div className="pg-reply-section pg-reply-attention-section">
          <div className="pg-reply-attention-head">
            <span className="pg-reply-attention-title">Attention Set</span>
            <span className="pg-reply-attention-sub">
              Select who receives attention upon sending this reply
            </span>
          </div>
          <div
            className="pg-reply-attention-chips"
            id={`pgReplyAttentionChips-${num}`}
            dangerouslySetInnerHTML={{ __html: attentionChipsHtml }}
          />
          <div className="pg-reply-attention-reason-row">
            <input
              type="text"
              id="pgReplyAttentionReasonInput"
              className="pg-reply-attention-reason-input"
              placeholder="Reason for attention set update (optional)"
              defaultValue=""
            />
          </div>
        </div>
        <div
          className="pg-reply-section"
          dangerouslySetInnerHTML={{ __html: labelScoresTableHtml }}
        />
        <div className="pg-reply-footer">
          <span className="pg-reply-attn-note">{policyNote}</span>
          <div className="pg-reply-footer-btns">
            <button
              id="btnDiscardAllDraftsFooter"
              type="button"
              className="pg-btn-flat pg-btn-discard-drafts"
              style={{ display: 'none' }}
              onClick={'discardAllReplyDialogDrafts(state.selectedClNumber)' as unknown as React.MouseEventHandler}
              title="Discard all draft comments on this change"
            >
              Discard All Drafts
            </button>
            <button
              id="btnResetStagedVotes"
              type="button"
              className="pg-btn-flat"
              style={{ display: 'none' }}
              onClick={'resetStagedVote()' as unknown as React.MouseEventHandler}
              title="Reset staged scores"
            >
              Reset
            </button>
            <button
              id="btnCancelReplyDialog"
              type="button"
              className="pg-btn-flat"
              onClick={'cancelReplyDialog()' as unknown as React.MouseEventHandler}
              title="Cancel and close reply dialog (Esc)"
            >
              Cancel
            </button>
            <button
              id="btnSubmitVotes"
              type="button"
              className="pg-btn-primary btn-submit-votes"
              onClick={'submitStagedVotes(this)' as unknown as React.MouseEventHandler}
              disabled
              title="Send reply and votes to Gerrit (Enter / Ctrl+Enter)"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export interface ChecksViewProps {
  num: number;
  ps: number;
  isLoading: boolean;
  errorMsg?: string;
  checksUrl?: string;
  workplanUrl?: string;
  workplanId?: string;
  isEmptyTargets: boolean;
  viewerPresubmitReady: boolean;
  bannerStatusClass: string;
  bannerIcon: string;
  bannerTitle: string;
  liveTargetsSummaryText: string;
  failWarnCnt: number;
  activeCnt: number;
  totalCnt: number;
  passedCnt: number;
  filterId: string;
  kindFilter: string;
  kindCounts: { all: number; build: number; test: number; preflight: number };
  rawSearchQuery: string;
  filteredTargets: PresubmitTargetItem[];
  selectedTargetName?: string;
  detailHtml: string;
}

export function ChecksView(props: ChecksViewProps): React.ReactElement {
  const {
    num,
    ps,
    isLoading,
    errorMsg,
    checksUrl,
    workplanUrl,
    workplanId,
    isEmptyTargets,
    viewerPresubmitReady,
    bannerStatusClass,
    bannerIcon,
    bannerTitle,
    liveTargetsSummaryText,
    failWarnCnt,
    activeCnt,
    totalCnt,
    passedCnt,
    filterId,
    kindFilter,
    kindCounts,
    rawSearchQuery,
    filteredTargets,
    selectedTargetName,
    detailHtml,
  } = props;

  if (isLoading) {
    return (
      <div className="pg-checks-view">
        <div className="pg-checks-loading">
          <span
            className="btn-spinner"
            style={{ width: '20px', height: '20px', borderWidth: '2.5px' }}
          ></span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontWeight: 600, fontSize: '13px', color: 'var(--g-fg)' }}>
              {`Querying TreeHugger presubmit runs for ag/${num} (Patchset ${ps})…`}
            </span>
            <span style={{ fontSize: '11.5px', color: 'var(--g-muted)' }}>
              Fetching build and test targets from Android Build
            </span>
          </div>
        </div>
      </div>
    );
  }

  if (errorMsg) {
    return (
      <div className="pg-checks-view">
        <div className="pg-checks-error">
          <div style={{ fontWeight: 600, fontSize: '13px', color: '#f28b82' }}>
            Failed to load TreeHugger presubmit checks
          </div>
          <div style={{ fontSize: '12px', color: 'var(--g-muted)' }}>{errorMsg}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
            <button
              type="button"
              className="pg-btn-primary"
              onClick={`event.stopPropagation(); fetchPresubmitTargetsForCl(${num}, { forceRefresh: true })` as unknown as React.MouseEventHandler}
            >
              ↻ Retry
            </button>
            {checksUrl ? (
              <a
                className="ag-link"
                href={checksUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open Gerrit Checks ↗
              </a>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  if (isEmptyTargets) {
    return (
      <div className="pg-checks-view">
        <div className="pg-checks-empty-card">
          <div style={{ fontSize: '28px' }}>⏳</div>
          <div style={{ fontWeight: 600, fontSize: '14px', color: 'var(--g-fg)' }}>
            {`No TreeHugger checks recorded yet for ag/${num} (Patchset ${ps})`}
          </div>
          <div
            style={{
              fontSize: '12px',
              color: 'var(--g-muted)',
              maxWidth: '500px',
              textAlign: 'center',
            }}
          >
            TreeHugger runs automatically on Android Build once Presubmit-Ready +1 is cast by an
            authorized reviewer.
          </div>
          <div style={{ marginTop: '8px' }}>
            {!viewerPresubmitReady ? (
              <button
                type="button"
                className="pg-btn-primary"
                onClick={'event.stopPropagation(); toggleStagedPr(true)' as unknown as React.MouseEventHandler}
              >
                ☑ Stage Presubmit-Ready (PR +1)
              </button>
            ) : (
              <span style={{ color: '#fdd663', fontWeight: 600, fontSize: '12px' }}>
                ⏳ TreeHugger workplan is allocating build &amp; test targets…
              </span>
            )}
          </div>
        </div>
      </div>
    );
  }

  const searchQueryLower = String(rawSearchQuery || '').trim().toLowerCase();

  return (
    <div className="pg-checks-view">
      <div className={`pg-checks-banner ${bannerStatusClass}`}>
        <div className="pg-checks-banner-left">
          <span className="pg-checks-banner-icon">{bannerIcon}</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            <span className="pg-checks-banner-title">{bannerTitle}</span>
            <span className="pg-checks-banner-sub">
              {`Patchset ${ps} · ${liveTargetsSummaryText}${workplanId ? ` · Workplan ${workplanId}` : ''}`}
            </span>
          </div>
        </div>
        <div className="pg-checks-banner-right">
          {workplanUrl ? (
            <a
              className="ag-link"
              style={{ fontSize: '12px' }}
              href={workplanUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Workplan ↗
            </a>
          ) : null}
          {checksUrl ? (
            <a
              className="ag-link"
              style={{ fontSize: '12px' }}
              href={checksUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Gerrit Checks ↗
            </a>
          ) : null}
          {failWarnCnt > 0 ? (
            <button
              type="button"
              className="pg-btn-rerun-failed"
              onClick={`openPgChecksRerunModal(${num}, { mode: 'failed' })` as unknown as React.MouseEventHandler}
              title="Rerun all failed presubmit checks"
            >
              {`↻ Rerun Failed (${failWarnCnt})`}
            </button>
          ) : null}
          <button
            type="button"
            className="pg-btn-subtle"
            onClick={`openPgChecksRerunModal(${num}, { mode: 'all' })` as unknown as React.MouseEventHandler}
            title="Rerun presubmit checks (Shortcut: r)"
          >
            ↻ Rerun…
          </button>
          <button
            type="button"
            className="pg-btn-subtle"
            onClick={`fetchPresubmitTargetsForCl(${num}, { forceRefresh: true })` as unknown as React.MouseEventHandler}
            title="Refresh runs"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      <div className="pg-checks-filter-bar">
        <div className="pg-checks-filter-tabs">
          <button
            type="button"
            className={`pg-checks-filter-tab ${filterId === 'all' ? 'active' : ''}`}
            onClick={`setPgChecksCategoryFilter(${num}, 'all')` as unknown as React.MouseEventHandler}
          >
            {`All (${totalCnt})`}
          </button>
          <button
            type="button"
            className={`pg-checks-filter-tab ${filterId === 'failed' ? 'active' : ''}`}
            onClick={`setPgChecksCategoryFilter(${num}, 'failed')` as unknown as React.MouseEventHandler}
          >
            {`Failed (${failWarnCnt})`}
          </button>
          <button
            type="button"
            className={`pg-checks-filter-tab ${filterId === 'running' ? 'active' : ''}`}
            onClick={`setPgChecksCategoryFilter(${num}, 'running')` as unknown as React.MouseEventHandler}
          >
            {`Running (${activeCnt})`}
          </button>
          <button
            type="button"
            className={`pg-checks-filter-tab ${filterId === 'passed' ? 'active' : ''}`}
            onClick={`setPgChecksCategoryFilter(${num}, 'passed')` as unknown as React.MouseEventHandler}
          >
            {`Passed (${passedCnt})`}
          </button>
        </div>
        <div className="pg-checks-search-wrap">
          <span className="pg-checks-search-icon">🔍</span>
          <input
            id={`pgChecksSearchInput-${num}`}
            type="text"
            className="pg-checks-search-input"
            value={rawSearchQuery || ''}
            onInput={`onPgChecksSearchInput(${num}, this.value)` as unknown as React.FormEventHandler}
            placeholder="Filter checks (e.g. goldfish, test, build)..."
            aria-label="Filter checks"
          />
          {searchQueryLower ? (
            <button
              type="button"
              className="pg-checks-search-clear"
              onClick={`clearPgChecksSearch(${num})` as unknown as React.MouseEventHandler}
              title="Clear filter"
            >
              ✕
            </button>
          ) : null}
        </div>
      </div>

      <div className="pg-checks-kind-tabs" id={`pgChecksKindTabs-${num}`}>
        <span className="pg-checks-kind-label">Kind:</span>
        <button
          type="button"
          className={`pg-checks-kind-tab ${kindFilter === 'all' ? 'active' : ''}`}
          onClick={`setPgChecksKindFilter(${num}, 'all')` as unknown as React.MouseEventHandler}
        >
          {`All (${kindCounts.all})`}
        </button>
        <button
          type="button"
          className={`pg-checks-kind-tab ${kindFilter === 'build' ? 'active' : ''}`}
          onClick={`setPgChecksKindFilter(${num}, 'build')` as unknown as React.MouseEventHandler}
        >
          {`🏗️ Build (${kindCounts.build})`}
        </button>
        <button
          type="button"
          className={`pg-checks-kind-tab ${kindFilter === 'test' ? 'active' : ''}`}
          onClick={`setPgChecksKindFilter(${num}, 'test')` as unknown as React.MouseEventHandler}
        >
          {`🧪 Test (${kindCounts.test})`}
        </button>
        <button
          type="button"
          className={`pg-checks-kind-tab ${kindFilter === 'preflight' ? 'active' : ''}`}
          onClick={`setPgChecksKindFilter(${num}, 'preflight')` as unknown as React.MouseEventHandler}
        >
          {`⚡ Preflight (${kindCounts.preflight})`}
        </button>
      </div>

      {filteredTargets.length === 0 ? (
        <div className="pg-checks-empty-filtered">
          <span>{`No checks match "${searchQueryLower || filterId}".`}</span>
          <button
            type="button"
            className="pg-btn-subtle"
            style={{ marginLeft: '8px' }}
            onClick={`clearPgChecksSearch(${num})` as unknown as React.MouseEventHandler}
          >
            Clear Filter
          </button>
        </div>
      ) : (
        <div className="pg-checks-grid">
          <div className="pg-checks-runs-col">
            <div className="pg-checks-runs-header">
              <span>{`Runs (${filteredTargets.length})`}</span>
              <div className="pg-checks-runs-header-actions">
                {failWarnCnt > 0 ? (
                  <button
                    type="button"
                    className="pg-btn-subtle-rerun"
                    id={`btnChecksRerunFailed-${num}`}
                    onClick={`openPgChecksRerunModal(${num}, { mode: 'failed' })` as unknown as React.MouseEventHandler}
                    title="Rerun failed checks"
                  >
                    {`↻ Failed (${failWarnCnt})`}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="pg-btn-subtle"
                  id={`btnChecksRerunAll-${num}`}
                  onClick={`openPgChecksRerunModal(${num})` as unknown as React.MouseEventHandler}
                  title="Rerun checks menu (Shortcut: r)"
                >
                  ↻ Rerun
                </button>
              </div>
            </div>
            <div className="pg-checks-runs-list">
              {filteredTargets.map((t, idx) => {
                const isSel = Boolean(selectedTargetName && selectedTargetName === t.name);
                const kindLabel = (t.kind && PRESUBMIT_KIND_LABELS[t.kind]) || 'TARGET';
                const durText = t.duration_display || '';
                const safeName = escapeJsQuote(t.name || '');
                return (
                  <div
                    key={idx}
                    className={`pg-checks-run-item ${isSel ? 'is-selected' : ''}`}
                    onClick={`selectPgCheckRunByIndex(${num}, ${idx})` as unknown as React.MouseEventHandler}
                  >
                    <div className="pg-checks-run-item-left">
                      <PresubmitStatusPill t={t} />
                      <span className="pg-checks-kind-badge">{kindLabel}</span>
                      <span className="pg-checks-run-name" title={t.name}>
                        {t.name}
                      </span>
                    </div>
                    <div className="pg-checks-run-item-right">
                      {durText ? <span className="pg-checks-run-dur">{durText}</span> : null}
                      <button
                        type="button"
                        className="pg-btn-run-rerun"
                        onClick={`event.stopPropagation(); triggerPgSingleCheckRerun(${num}, '${safeName}')` as unknown as React.MouseEventHandler}
                        title={`Rerun ${t.name}`}
                      >
                        ↻
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          <div
            className="pg-checks-detail-col"
            dangerouslySetInnerHTML={{ __html: detailHtml }}
          />
        </div>
      )}
    </div>
  );
}

export interface SavedSearchCardItem {
  id: string;
  title: string;
  query: string;
  showOnDashboard?: boolean;
  matchCount: number;
}

export function SavedSearchesList(props: { items: SavedSearchCardItem[] }): React.ReactElement {
  const { items } = props;
  if (!items || items.length === 0) {
    return (
      <div style={{ padding: '24px', textAlign: 'center', color: 'var(--g-muted)' }}>
        No saved searches or custom dashboard sections configured. Use the form above to add one,
        or click Reset Defaults.
      </div>
    );
  }
  return (
    <>
      {items.map((s, idx) => {
        const safeId = escapeJsQuote(s.id);
        return (
          <div
            key={s.id}
            className={`pg-saved-search-card ${s.showOnDashboard ? 'is-active-on-dash' : ''}`}
            id={`pgSavedSearchCard-${s.id}`}
          >
            <div className="pg-saved-search-card-left">
              <div className="pg-saved-search-reorder-btns">
                <button
                  type="button"
                  className="pg-btn-reorder"
                  onClick={`movePgSavedSearch('${safeId}', -1)` as unknown as React.MouseEventHandler}
                  title="Move up"
                  disabled={idx === 0}
                >
                  ▲
                </button>
                <button
                  type="button"
                  className="pg-btn-reorder"
                  onClick={`movePgSavedSearch('${safeId}', 1)` as unknown as React.MouseEventHandler}
                  title="Move down"
                  disabled={idx === items.length - 1}
                >
                  ▼
                </button>
              </div>
              <div className="pg-saved-search-info">
                <div className="pg-saved-search-title-row">
                  <span className="pg-saved-search-name">{s.title}</span>
                  {s.matchCount > 0 ? (
                    <span className="pg-saved-search-match-badge has-matches">
                      {`${s.matchCount} match${s.matchCount === 1 ? '' : 'es'}`}
                    </span>
                  ) : (
                    <span className="pg-saved-search-match-badge">0 matches</span>
                  )}
                </div>
                <div className="pg-saved-search-query-text" title={s.query}>
                  {s.query}
                </div>
              </div>
            </div>
            <div className="pg-saved-search-card-right">
              <label
                className="pg-saved-search-check-label"
                title="Toggle whether this section appears on your PolyGerrit dashboard"
              >
                <input
                  type="checkbox"
                  checked={Boolean(s.showOnDashboard)}
                  onChange={`toggleSavedSearchDashboardVisibility('${safeId}')` as unknown as React.ChangeEventHandler}
                />
                <span>Dashboard</span>
              </label>
              <button
                type="button"
                className="btn pg-btn-flat"
                onClick={`applyPgSavedSearch('${safeId}')` as unknown as React.MouseEventHandler}
                title="Run query in search bar"
              >
                ▶ Run
              </button>
              <button
                type="button"
                className="btn pg-btn-flat"
                onClick={`deletePgSavedSearch('${safeId}')` as unknown as React.MouseEventHandler}
                title="Delete this saved search"
              >
                🗑
              </button>
            </div>
          </div>
        );
      })}
    </>
  );
}

export interface PresubmitCounts {
  total?: number;
  failed?: number;
  warning?: number;
  running?: number;
  queued?: number;
  passed?: number;
  cached?: number;
}

export interface PresubmitTargetsEntry {
  loading?: boolean;
  error?: string;
  data?: {
    patch_set?: number;
    counts?: PresubmitCounts;
    targets?: PresubmitTargetItem[];
  } | null;
}

export interface PresubmitDisplayInfo {
  status?: string;
  workplanId?: string;
  workplanUrl?: string;
  checksUrl?: string;
}

export function filterPresubmitTargetsByCategory(
  targets: PresubmitTargetItem[],
  filterId: string
): PresubmitTargetItem[] {
  if (!filterId || filterId === 'all') return targets;
  if (filterId === 'failed') {
    return targets.filter((t) => t.status === 'failed' || t.status === 'warning');
  }
  if (filterId === 'running') {
    return targets.filter((t) => t.status === 'running' || t.status === 'queued');
  }
  if (filterId === 'passed') {
    return targets.filter((t) => t.status === 'passed');
  }
  return targets;
}

export function renderPresubmitStatusPillHtml(t: PresubmitTargetItem): string {
  return renderReactToHtml(<PresubmitStatusPill t={t || { name: '' }} />);
}

export function renderSinglePresubmitTargetRowHtml(
  t: PresubmitTargetItem,
  fallbackChecksUrl?: string
): string {
  return renderReactToHtml(
    <SinglePresubmitTargetRow
      t={t || { name: '' }}
      fallbackChecksUrl={fallbackChecksUrl}
    />
  );
}

export function renderPgChecksTabButtonHtml(
  c: { number?: number } | null | undefined,
  activeTab: string,
  entry?: PresubmitTargetsEntry | null,
  pv?: PresubmitDisplayInfo | null
): string {
  const num = Number(c && c.number ? c.number : 0);
  const isActive = activeTab === 'checks';
  const counts = entry?.data?.counts || null;
  let badgeClass = '';
  let badgeText = '';
  if (counts && (counts.total || 0) > 0) {
    if ((counts.failed || 0) > 0) {
      badgeClass = 'st-failed';
      badgeText = `✕ ${counts.failed}`;
    } else if ((counts.warning || 0) > 0) {
      badgeClass = 'st-warning';
      badgeText = `⚠ ${counts.warning}`;
    } else if ((counts.running || 0) > 0 || (counts.queued || 0) > 0) {
      badgeClass = 'st-running';
      badgeText = `◷ ${(counts.running || 0) + (counts.queued || 0)}`;
    } else if ((counts.passed || 0) > 0) {
      badgeClass = 'st-passed';
      badgeText = `✓ ${counts.passed}`;
    } else {
      badgeText = String(counts.total);
    }
  } else if (pv?.status === 'failed') {
    badgeClass = 'st-failed';
    badgeText = '✕ Fail';
  } else if (pv?.status === 'warning') {
    badgeClass = 'st-warning';
    badgeText = '⚠ Warn';
  } else if (pv?.status === 'running' || pv?.status === 'queued') {
    badgeClass = 'st-running';
    badgeText = '◷ Run';
  } else if (pv?.status === 'passed') {
    badgeClass = 'st-passed';
    badgeText = '✓ Pass';
  }
  return renderReactToHtml(
    <ChecksTabButton
      num={num}
      isActive={isActive}
      badgeClass={badgeClass}
      badgeText={badgeText}
    />
  );
}

export function renderPgChecksControlsHtml(
  c: { number?: number; viewer_presubmit_ready?: boolean } | null | undefined,
  entry?: PresubmitTargetsEntry | null,
  pv?: PresubmitDisplayInfo | null
): string {
  const num = Number(c && c.number ? c.number : 0);
  const isRefreshing = Boolean(entry && entry.loading);
  const showStagePrBtn = Boolean(!c?.viewer_presubmit_ready && pv?.status !== 'passed');
  return renderReactToHtml(
    <ChecksControls
      num={num}
      isRefreshing={isRefreshing}
      showStagePrBtn={showStagePrBtn}
      workplanUrl={pv?.workplanUrl}
      workplanId={pv?.workplanId}
      checksUrl={pv?.checksUrl}
    />
  );
}

export function renderPgCheckRunLogDrawerHtml(
  c: { number?: number },
  t: PresubmitTargetItem,
  _pv: PresubmitDisplayInfo | null | undefined,
  isLogOpen: boolean,
  rawLogSearch: string,
  allLines: PresubmitLogLine[]
): string {
  const num = Number(c.number || 0);
  const logSearchLower = String(rawLogSearch || '').trim().toLowerCase();
  const visibleLines = logSearchLower
    ? (allLines || []).filter((l) => l.text.toLowerCase().includes(logSearchLower))
    : allLines || [];
  return renderReactToHtml(
    <CheckRunLogDrawer
      num={num}
      target={t}
      isLogOpen={isLogOpen}
      rawLogSearch={rawLogSearch || ''}
      visibleLines={visibleLines}
    />
  );
}

export function renderPgCheckDetailCardHtml(
  c: { number?: number; patch_set?: number },
  t: PresubmitTargetItem,
  pv: PresubmitDisplayInfo | null | undefined,
  isLogOpen: boolean,
  rawLogSearch: string,
  allLines: PresubmitLogLine[]
): string {
  const num = Number(c.number || 0);
  const ps = Number(c.patch_set || 1);
  const logDrawerHtml = renderPgCheckRunLogDrawerHtml(
    c,
    t,
    pv,
    isLogOpen,
    rawLogSearch,
    allLines
  );
  return renderReactToHtml(
    <CheckDetailCard
      num={num}
      ps={ps}
      target={t}
      checksUrl={pv?.checksUrl}
      isLogOpen={isLogOpen}
      logDrawerHtml={logDrawerHtml}
    />
  );
}

export function renderPgChecksViewComponentHtml(
  c: { number?: number; patch_set?: number; viewer_presubmit_ready?: boolean },
  entry: PresubmitTargetsEntry | null | undefined,
  pv: PresubmitDisplayInfo | null | undefined,
  filterId: string,
  kindFilter: string,
  rawSearchQuery: string,
  selectedRunName: string,
  isLogOpen: boolean,
  rawLogSearch: string,
  liveTargetsSummaryText: string,
  getLogLines: (t: PresubmitTargetItem) => PresubmitLogLine[]
): string {
  const num = Number(c.number || 0);
  const ps = Number(c.patch_set || 1);
  const tData = entry?.data || null;
  const counts = tData?.counts || {
    total: 0,
    failed: 0,
    warning: 0,
    running: 0,
    queued: 0,
    passed: 0,
    cached: 0,
  };
  const targets = tData?.targets || [];
  const isLoading = !entry || Boolean(entry.loading && !entry.data);
  const errorMsg = entry && entry.error && !entry.data ? entry.error : undefined;
  const isEmptyTargets = !isLoading && !errorMsg && targets.length === 0;

  const failWarnCnt = (counts.failed || 0) + (counts.warning || 0);
  const activeCnt = (counts.running || 0) + (counts.queued || 0);
  const totalCnt = counts.total || 0;
  const passedCnt = counts.passed || 0;

  const kindCounts = {
    all: targets.length,
    build: targets.filter((t) => String(t.kind || '').toLowerCase().includes('build')).length,
    test: targets.filter((t) => String(t.kind || '').toLowerCase().includes('test')).length,
    preflight: targets.filter((t) => {
      const k = String(t.kind || '').toLowerCase();
      return k.includes('preflight') || k.includes('check') || k.includes('lint');
    }).length,
  };

  let filtered = filterPresubmitTargetsByCategory(targets, filterId || 'all');
  if (kindFilter && kindFilter !== 'all') {
    filtered = filtered.filter((t) => {
      const k = String(t.kind || '').toLowerCase();
      if (kindFilter === 'build') return k.includes('build');
      if (kindFilter === 'test') return k.includes('test');
      if (kindFilter === 'preflight') {
        return k.includes('preflight') || k.includes('check') || k.includes('lint');
      }
      return true;
    });
  }
  const searchQuery = String(rawSearchQuery || '').trim().toLowerCase();
  if (searchQuery) {
    filtered = filtered.filter((t) => {
      const name = String(t.name || '').toLowerCase();
      const sub = String(t.subtitle || '').toLowerCase();
      const kind = String(t.kind || '').toLowerCase();
      const art = String(t.artifact_id || '').toLowerCase();
      return (
        name.includes(searchQuery) ||
        sub.includes(searchQuery) ||
        kind.includes(searchQuery) ||
        art.includes(searchQuery)
      );
    });
  }

  let selectedTarget: PresubmitTargetItem | null = null;
  if (selectedRunName) {
    selectedTarget = filtered.find((t) => t.name === selectedRunName) || null;
  }
  if (!selectedTarget && filtered.length > 0) {
    selectedTarget =
      filtered.find((t) => t.status === 'failed' || t.status === 'warning') ||
      filtered.find((t) => t.status === 'running' || t.status === 'queued') ||
      filtered[0];
  }

  let bannerStatusClass = 'banner-passed';
  let bannerIcon = '✓';
  let bannerTitle = `All ${targets.length} TreeHugger checks passed`;
  if ((counts.failed || 0) > 0) {
    bannerStatusClass = 'banner-failed';
    bannerIcon = '✕';
    bannerTitle = `${counts.failed} of ${targets.length} checks failed`;
  } else if ((counts.warning || 0) > 0) {
    bannerStatusClass = 'banner-warning';
    bannerIcon = '⚠';
    bannerTitle = `${counts.warning} checks with warnings`;
  } else if (activeCnt > 0) {
    bannerStatusClass = 'banner-running';
    bannerIcon = '◷';
    bannerTitle = `${activeCnt} checks running (${passedCnt} passed)`;
  }

  const detailHtml = selectedTarget
    ? renderPgCheckDetailCardHtml(
        c,
        selectedTarget,
        pv,
        isLogOpen,
        rawLogSearch,
        getLogLines ? getLogLines(selectedTarget) : []
      )
    : '<div style="padding:24px;color:var(--g-muted);font-size:12px;">Select a check run on the left to inspect its details and logs.</div>';

  return renderReactToHtml(
    <ChecksView
      num={num}
      ps={ps}
      isLoading={isLoading}
      errorMsg={errorMsg}
      checksUrl={pv?.checksUrl}
      workplanUrl={pv?.workplanUrl}
      workplanId={pv?.workplanId}
      isEmptyTargets={isEmptyTargets}
      viewerPresubmitReady={Boolean(c.viewer_presubmit_ready)}
      bannerStatusClass={bannerStatusClass}
      bannerIcon={bannerIcon}
      bannerTitle={bannerTitle}
      liveTargetsSummaryText={liveTargetsSummaryText}
      failWarnCnt={failWarnCnt}
      activeCnt={activeCnt}
      totalCnt={totalCnt}
      passedCnt={passedCnt}
      filterId={filterId || 'all'}
      kindFilter={kindFilter || 'all'}
      kindCounts={kindCounts}
      rawSearchQuery={rawSearchQuery || ''}
      filteredTargets={filtered}
      selectedTargetName={selectedTarget?.name}
      detailHtml={detailHtml}
    />
  );
}

export function renderPgSavedSearchesCardsHtml(items: SavedSearchCardItem[]): string {
  return renderReactToHtml(<SavedSearchesList items={items || []} />);
}

export function renderPgReplyDialogComponentHtml(
  c: { number?: number; patch_set?: number },
  replyDialogOpen: boolean,
  viewer: string,
  policyNote: string,
  initialComment: string,
  hasReplyDraft: boolean,
  participantsHtml: string,
  quickChipsHtml: string,
  markdownToolbarHtml: string,
  attentionChipsHtml: string,
  labelScoresTableHtml: string
): string {
  return renderReactToHtml(
    <ReplyDialog
      num={Number(c.number || 0)}
      patchSet={Number(c.patch_set || 1)}
      viewer={viewer || 'viewer'}
      replyDialogOpen={replyDialogOpen}
      policyNote={policyNote}
      initialComment={initialComment}
      hasReplyDraft={hasReplyDraft}
      participantsHtml={participantsHtml}
      quickChipsHtml={quickChipsHtml}
      markdownToolbarHtml={markdownToolbarHtml}
      attentionChipsHtml={attentionChipsHtml}
      labelScoresTableHtml={labelScoresTableHtml}
    />
  );
}
