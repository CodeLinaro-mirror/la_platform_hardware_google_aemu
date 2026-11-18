// Copyright (C) 2026 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import React from 'react';
import { escapeHtml, escapeJsQuote, renderReactToHtml } from '../runtime';
import type {
  GerritCumulativeStackFileItem,
  GerritStackSubmitSequence,
  GerritStackSubmitSequenceItem,
} from '../types';

const PERCENT_MULTIPLIER = 100;

export function StackSequenceHeroBanner({
  seq,
  stackId,
}: {
  seq: GerritStackSubmitSequence;
  stackId: string;
}): React.ReactElement {
  const escapedStackId = escapeHtml(stackId);
  if (seq.all_ready) {
    return (
      <>
        <div className="pg-stack-seq-hero-left">
          <span className="pg-stack-seq-hero-icon" aria-hidden="true">
            🎉
          </span>
          <div>
            <div className="pg-stack-seq-hero-title">
              Entire Stack Ready for Submission!
            </div>
            <div className="pg-stack-seq-hero-sub">
              {`All ${seq.total_commits} commits satisfy Gerrit submit requirements and can be submitted sequentially from bottom to top.`}
            </div>
          </div>
        </div>
        <button
          type="button"
          className="btn pg-btn-stack-action pg-btn-stack-approve"
          htmlOnClick={`approveStackForBatchEdit('${escapedStackId}')`}
          title={`Approve all ${seq.total_commits} commits with Code-Review +2`}
        >
          <span className="pg-btn-icon">⚡</span> +2 All Commits
        </button>
      </>
    );
  }
  const bottleneckNum = seq.bottleneck_commit?.number ?? '';
  return (
    <>
      <div className="pg-stack-seq-hero-left">
        <span className="pg-stack-seq-hero-icon" aria-hidden="true">
          ⚠️
        </span>
        <div>
          <div className="pg-stack-seq-hero-title">
            {`Submission Blocked at Step ${seq.bottleneck_step} (ag/${bottleneckNum})`}
          </div>
          <div className="pg-stack-seq-hero-sub">
            {'Next commit in sequence is blocked: '}
            <strong>{seq.bottleneck_reason || ''}</strong>
            {'. Commits above cannot be submitted until this is resolved.'}
          </div>
        </div>
      </div>
      {seq.bottleneck_commit ? (
        <button
          type="button"
          className="btn pg-btn-stack-action"
          htmlOnClick={`scrollToStackCommit(${seq.bottleneck_commit.number}); closePgStackSequenceDialog();`}
          title="Jump to blocking commit"
        >
          <span className="pg-btn-icon">👁️</span> Jump to Blocker
        </button>
      ) : null}
    </>
  );
}

export function StackSequenceCheckpointsBar({
  seq,
}: {
  seq: GerritStackSubmitSequence;
}): React.ReactElement {
  const pctRev = Math.round(
    (seq.reviewed_commits / Math.max(1, seq.total_commits)) * PERCENT_MULTIPLIER
  );
  const bottleneckNum = seq.bottleneck_commit?.number ?? '';

  return (
    <>
      <div className="pg-stack-seq-cp-item">
        <span className="pg-stack-seq-cp-label">Review Checkpoint:</span>
        <span className="pg-stack-seq-cp-val">
          {`${seq.reviewed_commits}/${seq.total_commits} reviewed (${pctRev}%)`}
        </span>
      </div>
      <span htmlStyle="color:var(--g-border);">·</span>
      <div className="pg-stack-seq-cp-item">
        <span className="pg-stack-seq-cp-label">Submit Checkpoint:</span>
        <span className="pg-stack-seq-cp-val">
          {`${seq.submittable_commits}/${seq.total_commits} submittable (${seq.seq_ready_count} in sequence)`}
        </span>
      </div>
      <span htmlStyle="color:var(--g-border);">·</span>
      <div className="pg-stack-seq-cp-item">
        <span className="pg-stack-seq-cp-label">Active Bottleneck:</span>
        <span
          className="pg-stack-seq-cp-val"
          htmlStyle={
            seq.all_ready ? 'color:var(--g-green);' : 'color:var(--g-amber);'
          }
        >
          {seq.all_ready
            ? 'None (All Ready ✓)'
            : `Step ${seq.bottleneck_step} (ag/${bottleneckNum})`}
        </span>
      </div>
    </>
  );
}

function SequenceRowStatusBadge({
  item,
}: {
  item: GerritStackSubmitSequenceItem;
}): React.ReactElement | null {
  if (item.seq_status === 'ready') {
    return (
      <span className="pg-stack-seq-step-badge is-ready">
        {`✓ Step ${item.step} Ready`}
      </span>
    );
  }
  if (item.seq_status === 'blocked_current') {
    return (
      <span className="pg-stack-seq-step-badge is-blocked">
        {`✕ Step ${item.step} Blocked: ${item.blocker_reason || ''}`}
      </span>
    );
  }
  if (item.seq_status === 'waiting_ancestor') {
    const ancNum = item.blocking_ancestor?.number ?? '';
    return (
      <span className="pg-stack-seq-step-badge is-waiting">
        {`⏳ Waiting on #${item.blocking_step} (ag/${ancNum})`}
      </span>
    );
  }
  if (item.seq_status === 'merged') {
    return <span className="pg-stack-seq-step-badge is-merged">Merged</span>;
  }
  return null;
}

export function StackSequenceTable({
  seq,
  selectedClNumber,
  stackId,
}: {
  seq: GerritStackSubmitSequence;
  selectedClNumber?: number | null;
  stackId: string;
}): React.ReactElement {
  const escapedStackId = escapeHtml(stackId);
  return (
    <table className="pg-stack-seq-table">
      <thead>
        <tr>
          <th htmlStyle="width:40px;text-align:center;">#</th>
          <th>Commit &amp; Subject</th>
          <th htmlStyle="width:110px;">Review</th>
          <th htmlStyle="width:65px;">CR</th>
          <th htmlStyle="width:75px;">Verified</th>
          <th>Submit Sequence Status</th>
          <th htmlStyle="width:75px;text-align:right;">Action</th>
        </tr>
      </thead>
      <tbody>
        {seq.items.map((item) => {
          const isCur = Number(selectedClNumber) === item.number;
          const numClass =
            item.seq_status === 'ready' || item.seq_status === 'merged'
              ? 'is-ready'
              : item.seq_status === 'blocked_current'
                ? 'is-blocked'
                : '';
          const crColor = item.cr_label.startsWith('+')
            ? 'var(--g-green)'
            : item.cr_label.startsWith('-')
              ? 'var(--g-red)'
              : 'var(--g-muted)';
          const vColor = item.v_label.includes('+')
            ? 'var(--g-green)'
            : 'var(--g-muted)';

          return (
            <tr
              key={item.number}
              className={`pg-stack-seq-row${isCur ? ' is-current' : ''}`}
            >
              <td htmlStyle="width:40px;text-align:center;">
                <span className={`pg-stack-seq-num-badge ${numClass}`}>
                  {item.step}
                </span>
              </td>
              <td>
                <div htmlStyle="display:flex;align-items:center;gap:6px;font-weight:600;">
                  <a
                    href={`#pgStackCommit-${item.number}`}
                    className="pg-cl-link"
                    htmlOnClick={`event.preventDefault(); scrollToStackCommit(${item.number}); closePgStackSequenceDialog();`}
                  >
                    {`ag/${item.number}`}
                  </a>
                  <span className="pg-branch-badge">{item.branch}</span>
                </div>
                <div
                  htmlStyle="font-size:11.5px;color:var(--g-text);margin-top:2px;"
                  title={item.subject}
                >
                  {item.subject}
                </div>
                <div htmlStyle="font-size:10.5px;color:var(--g-muted);margin-top:1px;">
                  {`Author: ${item.author}`}
                </div>
              </td>
              <td htmlStyle="width:110px;">
                <button
                  type="button"
                  className={`pg-btn-stack-reviewed${
                    item.is_reviewed ? ' is-reviewed' : ''
                  }`}
                  htmlOnClick={`togglePgStackCommitReviewed(${item.number}); renderPgStackSequenceDialogBodyDom('${escapedStackId}');`}
                  title="Toggle commit reviewed"
                >
                  <span className="pg-btn-icon">
                    {item.is_reviewed ? '✓' : '○'}
                  </span>
                  <span className="pg-btn-label">
                    {item.is_reviewed ? 'Reviewed' : 'Mark'}
                  </span>
                </button>
              </td>
              <td
                htmlStyle={`width:65px;font-weight:600;font-size:11.5px;color:${crColor};`}
              >
                {item.cr_label}
              </td>
              <td htmlStyle={`width:75px;font-size:11.5px;color:${vColor};`}>
                {item.v_label}
              </td>
              <td>
                <SequenceRowStatusBadge item={item} />
              </td>
              <td htmlStyle="width:75px;text-align:right;">
                <button
                  type="button"
                  className="btn pg-btn-subtle"
                  htmlOnClick={`scrollToStackCommit(${item.number}); closePgStackSequenceDialog();`}
                  title="Jump to commit in diff view"
                >
                  👁️ Inspect
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function StackFilesExtensionChips({
  extensions,
  activeExt,
}: {
  extensions: Array<{ ext: string; count: number }>;
  activeExt: string;
}): React.ReactElement {
  return (
    <>
      {extensions.map((entry) => {
        const isAct = activeExt === entry.ext;
        return (
          <button
            key={entry.ext}
            type="button"
            className={`pg-stack-ext-chip${isAct ? ' is-active' : ''}`}
            htmlOnClick={`setPgStackFilesFilterExt('${escapeHtml(entry.ext)}')`}
          >
            {`${entry.ext} (${entry.count})`}
          </button>
        );
      })}
    </>
  );
}

export function StackFilesTable({
  items,
  selectedClNumber,
  activeFilterText,
}: {
  items: GerritCumulativeStackFileItem[];
  selectedClNumber?: number | null;
  activeFilterText: string;
}): React.ReactElement {
  if (items.length === 0) {
    return (
      <div htmlStyle="padding:24px;text-align:center;color:var(--g-muted);font-size:13px;">
        {`No files match the active search filter "${activeFilterText}".`}
      </div>
    );
  }
  return (
    <table className="pg-stack-files-table">
      <thead>
        <tr>
          <th htmlStyle="width:36px;text-align:center;">Type</th>
          <th>File Path</th>
          <th htmlStyle="width:110px;">Lines Delta</th>
          <th>Touched in Commits</th>
          <th htmlStyle="width:105px;text-align:right;">Action</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => {
          const statusUpper = item.status || 'M';
          const statusCls = `status-${statusUpper.toLowerCase()}`;
          const firstOcc = item.occurrences[0];
          const firstCl = firstOcc?.number || selectedClNumber || 0;
          const quotedFile = escapeJsQuote(item.file);

          return (
            <tr
              key={item.file}
              className="pg-stack-files-row"
              data-file={item.file}
            >
              <td htmlStyle="width:36px;text-align:center;">
                <span className={`pg-file-status-badge ${statusCls}`}>
                  {statusUpper}
                </span>
              </td>
              <td>
                <div className="pg-stack-file-path-wrap">
                  {item.extension ? (
                    <span
                      className="pg-file-ext-badge"
                      htmlStyle="font-size:10px;padding:1px 5px;margin-right:6px;"
                    >
                      {item.extension}
                    </span>
                  ) : null}
                  <span className="pg-stack-file-path" title={item.file}>
                    {item.file}
                  </span>
                </div>
              </td>
              <td htmlStyle="width:110px;white-space:nowrap;">
                <span className="pg-files-add">{`+${item.additions}`}</span>
                <span className="pg-files-del">{`-${item.deletions}`}</span>
              </td>
              <td>
                <div className="pg-stack-commits-list">
                  {item.occurrences.map((occ) => (
                    <button
                      key={`${occ.number}-${occ.step}`}
                      type="button"
                      className="pg-stack-commit-chip"
                      htmlOnClick={`jumpToStackFileDiff(${occ.number}, '${quotedFile}')`}
                      title={`Step ${occ.step}: ag/${occ.number} (${
                        occ.subject || ''
                      }) · +${occ.additions}/-${occ.deletions}`}
                    >
                      <span>{`Step ${occ.step} (ag/${occ.number})`}</span>
                    </button>
                  ))}
                </div>
              </td>
              <td htmlStyle="width:105px;text-align:right;">
                <button
                  type="button"
                  className="btn pg-btn-subtle"
                  htmlOnClick={`jumpToStackFileDiff(${firstCl}, '${quotedFile}')`}
                  title="Jump directly to file diff in stack"
                >
                  Jump to diff ↗
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function renderPgStackSequenceHeroHtml(
  seq: GerritStackSubmitSequence,
  stackId = ''
): string {
  return renderReactToHtml(
    <StackSequenceHeroBanner seq={seq} stackId={stackId} />
  );
}

export function renderPgStackSequenceCheckpointsHtml(
  seq: GerritStackSubmitSequence
): string {
  return renderReactToHtml(<StackSequenceCheckpointsBar seq={seq} />);
}

export function renderPgStackSequenceTableHtml(
  seq: GerritStackSubmitSequence,
  selectedClNumber?: number | null,
  stackId = ''
): string {
  return renderReactToHtml(
    <StackSequenceTable
      seq={seq}
      selectedClNumber={selectedClNumber}
      stackId={stackId}
    />
  );
}

export function renderPgStackFilesExtChipsHtml(
  extensions: Array<{ ext: string; count: number }>,
  activeExt = ''
): string {
  return renderReactToHtml(
    <StackFilesExtensionChips extensions={extensions} activeExt={activeExt} />
  );
}

export function renderPgStackFilesTableHtml(
  items: GerritCumulativeStackFileItem[],
  selectedClNumber?: number | null,
  activeFilterText = ''
): string {
  return renderReactToHtml(
    <StackFilesTable
      items={items}
      selectedClNumber={selectedClNumber}
      activeFilterText={activeFilterText}
    />
  );
}
