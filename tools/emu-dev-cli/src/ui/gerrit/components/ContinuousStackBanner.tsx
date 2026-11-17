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
import { escapeHtml, renderReactToHtml } from '../runtime';
import { createHostBridge } from '../state/store';
import type {
  GerritBridgeAdapter,
  GerritChange,
  GerritStack,
  GerritStackSubmitSequenceItem,
} from '../types';

const DEFAULT_STACK_TITLE = 'Relation Stack';
const PERCENT_MULTIPLIER = 100;

export interface ContinuousStackBannerProps {
  activeStack?: GerritStack | null;
  commits?: GerritChange[];
  bridge?: GerritBridgeAdapter;
}

export interface StackCommitSequenceBarProps {
  change?: GerritChange | null;
  idx: number;
  totalCount: number;
  activeStack?: GerritStack | null;
  bridge?: GerritBridgeAdapter;
}

interface CumulativeDiffTotals {
  totalAdd: number;
  totalDel: number;
  uniqueFilesCount: number;
}

function computeStackDiffTotals(
  commits: GerritChange[],
  bridge: GerritBridgeAdapter
): CumulativeDiffTotals {
  let totalAdd = 0;
  let totalDel = 0;
  const seenFiles = new Set<string>();
  for (const commit of commits) {
    const bundle = bridge.getDiffBundle(Number(commit.number));
    const files = Array.isArray(bundle?.files) ? bundle.files : [];
    for (const fileEntry of files) {
      if (fileEntry.file) {
        seenFiles.add(fileEntry.file);
      }
      totalAdd += Number(fileEntry.additions || 0);
      totalDel += Number(fileEntry.deletions || 0);
    }
  }
  return {
    totalAdd,
    totalDel,
    uniqueFilesCount: seenFiles.size,
  };
}

function resolveStepperStatusIcon(
  commit: GerritChange,
  isSubmittable: boolean
): string {
  if (commit.mergeable === false) {
    return '▲';
  }
  if (commit.work_in_progress) {
    return 'WIP';
  }
  return isSubmittable ? '✓' : '';
}

function StackStepperRow({
  commits,
  currentNum,
  totalCommits,
  bridge,
}: {
  commits: GerritChange[];
  currentNum: number;
  totalCommits: number;
  bridge: GerritBridgeAdapter;
}): React.ReactElement {
  return (
    <div className="pg-stack-stepper">
      {commits.map((commit, idx) => {
        const num = Number(commit.number);
        const isCurrent = num === currentNum;
        const isReviewed = bridge.isStackCommitReviewed(num);
        const isSubmittable = bridge.isClSubmittable(commit);
        const statIcon = resolveStepperStatusIcon(commit, isSubmittable);
        const pillCls = [
          'pg-stack-step-pill',
          isCurrent ? 'active' : '',
          isReviewed ? 'is-reviewed' : '',
          isSubmittable ? 'ready' : '',
        ]
          .filter(Boolean)
          .join(' ');
        const revSuffix = isReviewed ? ' (Reviewed)' : '';

        return (
          <button
            key={num}
            type="button"
            className={pillCls}
            data-cl={num}
            htmlOnClick={`scrollToStackCommit(${num})`}
            title={`Commit ${idx + 1}/${totalCommits}: ag/${num} - ${
              commit.subject || ''
            }${revSuffix}`}
          >
            <span className="pg-step-idx">{idx + 1}</span>
            <span className="pg-step-cl">{`ag/${num}`}</span>
            {isReviewed ? (
              <span className="pg-step-rev-icon" title="Commit reviewed">
                ✓
              </span>
            ) : null}
            {statIcon ? <span className="pg-step-status">{statIcon}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export function ContinuousStackBanner({
  activeStack,
  commits,
  bridge,
}: ContinuousStackBannerProps): React.ReactElement | null {
  if (!activeStack || !Array.isArray(commits) || commits.length <= 1) {
    return null;
  }
  const activeBridge = bridge ?? createHostBridge();
  const totalCommits = commits.length;
  const { totalAdd, totalDel, uniqueFilesCount } = computeStackDiffTotals(
    commits,
    activeBridge
  );
  const selectedCl = activeBridge.getSelectedClNumber();
  const currentNum = Number(selectedCl || commits[0]?.number || 0);
  const currentIdx = commits.findIndex(
    (commit) => Number(commit.number) === currentNum
  );
  const reviewedCount = commits.filter((commit) =>
    activeBridge.isStackCommitReviewed(Number(commit.number))
  ).length;
  const pctReviewed = Math.round(
    (reviewedCount / totalCommits) * PERCENT_MULTIPLIER
  );
  const allReviewed = reviewedCount === totalCommits;
  const anyExpanded = commits.some(
    (commit) => !activeBridge.isStackCommitCollapsed(Number(commit.number))
  );
  const seq = activeBridge.getStackSubmitSequence(activeStack, commits);
  const stackId = activeStack.stack_id || '';
  const escapedStackId = escapeHtml(stackId);
  const stackTitle =
    activeStack.title || activeStack.stack_id || DEFAULT_STACK_TITLE;

  const canInterdiff = activeBridge.canStackInterdiff(commits);
  let isStackInterdiff = activeBridge.isStackInterdiffActive(commits, stackId);
  if (
    canInterdiff &&
    !isStackInterdiff &&
    activeBridge.getPersistedStackInterdiff(stackId)
  ) {
    activeBridge.ensureStackInterdiffRestored(activeStack);
    isStackInterdiff = true;
  }

  const bottleneckNum = seq.bottleneck_commit?.number ?? '';
  const seqTitle = seq.all_ready
    ? 'Entire stack is submittable in sequence! (Alt+T)'
    : `Bottleneck at Step ${seq.bottleneck_step}: ag/${bottleneckNum} (${
        seq.bottleneck_reason || ''
      }) (Alt+T)`;

  return (
    <div
      id="pgStackReviewBanner"
      className="pg-stack-review-banner"
      data-stack-id={stackId}
    >
      <div className="pg-stack-review-banner-top">
        <div className="pg-stack-review-banner-left">
          <span className="pg-stack-review-badge">📚 Continuous Stack Review</span>
          {isStackInterdiff ? (
            <span
              id="pgStackInterdiffBadge"
              className="pg-stack-interdiff-badge"
              title="Comparing patchsets PS n-1 → PS n across stack commits"
            >
              ⚡ Stack Interdiff (PS n-1)
            </span>
          ) : null}
          <span className="pg-stack-review-title" title={stackTitle}>
            {`${stackTitle} (${totalCommits} commits)`}
          </span>
          <span
            className="pg-stack-review-stats is-clickable"
            htmlOnClick={`openPgStackFilesDrawer('${escapedStackId}')`}
            title="Inspect cumulative stack files (Alt+F)"
          >
            <span className="pg-files-add">{`+${totalAdd}`}</span>{' '}
            <span className="pg-files-del">{`-${totalDel}`}</span>
            {` across ${uniqueFilesCount} files`}
          </span>
        </div>
        <div className="pg-stack-progress-block">
          <div className="pg-stack-progress-labels">
            <span id="pgStackProgressText" className="pg-stack-progress-text">
              {`${reviewedCount} of ${totalCommits} commits reviewed (${pctReviewed}%)`}
            </span>
            <span
              id="pgStackCompleteBadge"
              className="pg-stack-complete-badge"
              htmlStyle={allReviewed ? 'display:inline-flex;' : 'display:none;'}
            >
              🎉 Whole Stack Reviewed!
            </span>
            <span
              id="pgStackSequenceStatusBadge"
              className={`pg-stack-seq-status-badge ${
                seq.all_ready ? 'ready' : 'pending'
              }`}
              htmlOnClick={`openPgStackSequenceDialog('${escapedStackId}')`}
              title={seqTitle}
            >
              {seq.all_ready
                ? '⚡ Stack Ready'
                : `⏳ Step ${seq.bottleneck_step}/${totalCommits} Next`}
            </span>
          </div>
          <div className="pg-stack-progress-track">
            <div
              id="pgStackProgressFill"
              className="pg-stack-progress-fill"
              htmlStyle={`width: ${pctReviewed}%;`}
            ></div>
          </div>
        </div>
        <div className="pg-stack-review-actions">
          <button
            id="btnStackBatchApproveAll"
            type="button"
            className="btn pg-btn-stack-action pg-btn-stack-approve"
            htmlOnClick={`approveStackForBatchEdit('${escapedStackId}')`}
            title={`Approve all ${totalCommits} commits with Code-Review +2`}
          >
            <span className="pg-btn-icon">⚡</span> +2 All Commits
          </button>
          <button
            id="btnStackBatchSelectAll"
            type="button"
            className="btn pg-btn-stack-action"
            htmlOnClick={`selectStackForBatchEdit('${escapedStackId}')`}
            title={`Select all ${totalCommits} commits in this stack for Batch Edit`}
          >
            <span className="pg-btn-icon">✓</span> Batch Select All
          </button>
          <button
            id="btnToggleStackInterdiff"
            type="button"
            className={`btn pg-btn-stack-action pg-btn-stack-interdiff${
              isStackInterdiff ? ' is-active' : ''
            }`}
            htmlOnClick={`togglePgStackInterdiff('${escapedStackId}')`}
            title={
              isStackInterdiff
                ? 'Reset comparison across all commits in stack back to Base (Alt+I or b)'
                : 'Compare all multi-patchset commits in stack against their previous patchsets (Alt+I or b)'
            }
            disabled={!canInterdiff}
          >
            <span className="pg-btn-icon">{isStackInterdiff ? '✕' : '⚡'}</span>{' '}
            {isStackInterdiff ? 'Reset Stack vs Base' : 'Stack Interdiff (PS n-1)'}
          </button>
          <button
            id="btnStackToggleCollapseAll"
            type="button"
            className="btn pg-btn-stack-action"
            htmlOnClick="toggleAllStackCommitsCollapse()"
            title="Toggle collapse/expand all commit bodies in this stack"
          >
            {anyExpanded ? '▲ Collapse All' : '▼ Expand All'}
          </button>
          <button
            id="btnExitContinuousStack"
            type="button"
            className="btn pg-btn-stack-action pg-btn-stack-exit"
            htmlOnClick={`toggleContinuousStackDiff(event, '${escapedStackId}')`}
            title="Exit continuous stack review and focus single CL"
          >
            📄 Single CL View
          </button>
        </div>
      </div>
      <div className="pg-stack-review-banner-bottom">
        <div className="pg-stack-stepper-wrap">
          <button
            id="btnStackPrevCommit"
            type="button"
            className="btn pg-btn-stepper-nav"
            htmlOnClick="goToPrevStackCommit()"
            disabled={currentIdx <= 0}
            title="Jump to previous commit in stack (Shift+[)"
          >
            ◀ Prev
          </button>
          <StackStepperRow
            commits={commits}
            currentNum={currentNum}
            totalCommits={totalCommits}
            bridge={activeBridge}
          />
          <button
            id="btnStackNextCommit"
            type="button"
            className="btn pg-btn-stepper-nav"
            htmlOnClick="goToNextStackCommit()"
            disabled={currentIdx >= totalCommits - 1}
            title="Jump to next commit in stack (Shift+])"
          >
            Next ▶
          </button>
        </div>
        <div className="pg-stack-checklist-actions">
          <button
            id="btnStackFilesDrawer"
            type="button"
            className="btn pg-btn-stack-action pg-btn-stack-files"
            htmlOnClick={`openPgStackFilesDrawer('${escapedStackId}')`}
            title="Overview and jump to any file touched across the whole stack (Alt+F)"
          >
            <span className="pg-btn-icon">📁</span>{' '}
            {`Stack Files (${uniqueFilesCount})`}
          </button>
          <button
            id="btnStackSubmitSeq"
            type="button"
            className={`btn pg-btn-stack-action pg-btn-stack-seq${
              seq.all_ready ? ' is-ready' : ''
            }`}
            htmlOnClick={`openPgStackSequenceDialog('${escapedStackId}')`}
            title="Inspect stack submit sequence & checkpoints (Alt+T)"
          >
            <span className="pg-btn-icon">{seq.all_ready ? '✓' : '📋'}</span>{' '}
            {`Submit Sequence (${seq.seq_ready_count}/${totalCommits} Ready)`}
          </button>
          <button
            id="btnStackMarkAllReviewed"
            type="button"
            className="pg-btn-text-action"
            htmlOnClick={
              allReviewed
                ? `resetStackCommitsReviewed('${escapedStackId}')`
                : `markAllStackCommitsReviewed('${escapedStackId}')`
            }
          >
            {allReviewed ? 'Reset Reviews' : 'Mark All Reviewed ✓'}
          </button>
        </div>
      </div>
    </div>
  );
}

function SequenceStepStatusBadge({
  itemSeq,
  idx,
  totalCount,
}: {
  itemSeq?: GerritStackSubmitSequenceItem | null;
  idx: number;
  totalCount: number;
}): React.ReactElement | null {
  if (!itemSeq) {
    return null;
  }
  const stepNum = idx + 1;
  if (itemSeq.seq_status === 'ready') {
    return (
      <span
        className="pg-stack-seq-step-badge is-ready"
        title={`Step ${stepNum} of ${totalCount}: Ready to submit in topological sequence`}
      >
        {`✓ Step ${stepNum} Ready`}
      </span>
    );
  }
  if (itemSeq.seq_status === 'blocked_current') {
    const reason = itemSeq.blocker_reason || '';
    return (
      <span
        className="pg-stack-seq-step-badge is-blocked"
        title={`Step ${stepNum} of ${totalCount} is blocking stack progress: ${reason}`}
      >
        {`✕ Step ${stepNum} Blocked: ${reason}`}
      </span>
    );
  }
  if (itemSeq.seq_status === 'waiting_ancestor') {
    const ancNum = itemSeq.blocking_ancestor?.number ?? '';
    return (
      <span
        className="pg-stack-seq-step-badge is-waiting"
        title={`Step ${stepNum} of ${totalCount}: Waiting on parent ag/${ancNum} (Step ${itemSeq.blocking_step})`}
      >
        {`⏳ Step ${stepNum} Waiting (on ag/${ancNum})`}
      </span>
    );
  }
  if (itemSeq.seq_status === 'merged') {
    return (
      <span
        className="pg-stack-seq-step-badge is-merged"
        title={`Step ${stepNum} of ${totalCount}: Already merged`}
      >
        Merged
      </span>
    );
  }
  return null;
}

export function StackCommitSequenceBar({
  change,
  idx,
  totalCount,
  activeStack,
  bridge,
}: StackCommitSequenceBarProps): React.ReactElement | null {
  if (!change) {
    return null;
  }
  const activeBridge = bridge ?? createHostBridge();
  const num = Number(change.number);
  const commits = Array.isArray(activeStack?.commits) ? activeStack.commits : [];
  const isSubmittable = activeBridge.isClSubmittable(change);
  const isReviewed = activeBridge.isStackCommitReviewed(num);
  const isCollapsed = activeBridge.isStackCommitCollapsed(num);

  const bundle = activeBridge.getDiffBundle(num);
  const bundleFiles = Array.isArray(bundle?.files) ? bundle.files : null;
  const fileCount = bundleFiles
    ? bundleFiles.length
    : Array.isArray(change.files)
      ? change.files.length
      : 0;
  const adds = bundleFiles
    ? bundleFiles.reduce((acc, f) => acc + Number(f.additions || 0), 0)
    : 0;
  const dels = bundleFiles
    ? bundleFiles.reduce((acc, f) => acc + Number(f.deletions || 0), 0)
    : 0;
  const summaryText = `Collapsed · ${fileCount} file${
    fileCount === 1 ? '' : 's'
  } (+${adds}/-${dels}) · Click to expand`;

  const seq = activeBridge.getStackSubmitSequence(activeStack, commits);
  const itemSeq = Array.isArray(seq?.items)
    ? seq.items.find((it) => it.number === num)
    : null;
  const prevCommit = idx > 0 ? commits[idx - 1] : null;
  const nextCommit = idx < totalCount - 1 ? commits[idx + 1] : null;

  return (
    <div
      className={`pg-stack-commit-seq-bar${isCollapsed ? ' is-collapsed' : ''}`}
      htmlOnClick={`togglePgStackCommitCollapse(${num})`}
    >
      <div className="pg-stack-commit-seq-left">
        <button
          id={`btnToggleStackCommitCollapse-${num}`}
          type="button"
          className="pg-btn-stack-commit-toggle"
          htmlOnClick={`event.stopPropagation(); togglePgStackCommitCollapse(${num})`}
          title={isCollapsed ? 'Expand commit diff' : 'Collapse commit diff'}
        >
          <span className="pg-stack-collapse-icon">
            {isCollapsed ? '▶' : '▼'}
          </span>
        </button>
        <div className="pg-stack-commit-seq-pill">
          <span>{`Commit ${idx + 1} of ${totalCount}`}</span>
          <span className="pg-seq-sep">·</span>
          <a
            href={`#pgStackCommit-${num}`}
            className="pg-seq-cl-link"
            htmlOnClick={`event.preventDefault(); event.stopPropagation(); scrollToStackCommit(${num})`}
          >
            {`ag/${num}`}
          </a>
          {isSubmittable ? (
            <span className="pg-status-badge status-submittable">✓ Ready</span>
          ) : (
            <span className="pg-status-badge status-review">In Review</span>
          )}
          <SequenceStepStatusBadge
            itemSeq={itemSeq}
            idx={idx}
            totalCount={totalCount}
          />
        </div>
        <button
          id={`btnToggleStackCommitReviewed-${num}`}
          type="button"
          className={`pg-btn-stack-reviewed${isReviewed ? ' is-reviewed' : ''}`}
          htmlOnClick={`event.stopPropagation(); togglePgStackCommitReviewed(${num})`}
          title={
            isReviewed
              ? `ag/${num}: Reviewed (click to mark unreviewed)`
              : `Mark commit ag/${num} as reviewed (Shift+Enter)`
          }
        >
          <span className="pg-btn-icon" aria-hidden="true">
            {isReviewed ? '✓' : '○'}
          </span>
          <span className="pg-btn-label">
            {isReviewed ? 'Commit Reviewed' : 'Mark Reviewed'}
          </span>
        </button>
        <span
          id={`pgStackCommitSummary-${num}`}
          className="pg-stack-collapsed-summary"
          htmlStyle={isCollapsed ? '' : 'display:none;'}
        >
          {summaryText}
        </span>
      </div>
      <div className="pg-stack-commit-seq-right">
        <div className="pg-stack-seq-nav">
          {prevCommit ? (
            <button
              type="button"
              className="pg-btn-stack-nav"
              htmlOnClick={`event.stopPropagation(); scrollToStackCommit(${prevCommit.number})`}
              title={`Jump to previous commit: ag/${prevCommit.number}`}
            >
              {`◀ Prev (ag/${prevCommit.number})`}
            </button>
          ) : null}
          {nextCommit ? (
            <button
              type="button"
              className="pg-btn-stack-nav"
              htmlOnClick={`event.stopPropagation(); scrollToStackCommit(${nextCommit.number})`}
              title={`Jump to next commit: ag/${nextCommit.number}`}
            >
              {`Next (ag/${nextCommit.number}) ▶`}
            </button>
          ) : null}
        </div>
        <span className="pg-seq-subject" title={change.subject || ''}>
          {change.subject || ''}
        </span>
        <a
          href="javascript:void(0)"
          className="pg-stack-commit-jump-top"
          htmlOnClick="event.stopPropagation(); scrollDiffToTop()"
          title="Jump to top of stack"
        >
          ↑ Top of Stack
        </a>
      </div>
    </div>
  );
}

export function renderPgContinuousStackBannerHtml(
  activeStack?: GerritStack | null,
  commits?: GerritChange[],
  bridge?: GerritBridgeAdapter
): string {
  return renderReactToHtml(
    <ContinuousStackBanner
      activeStack={activeStack}
      commits={commits}
      bridge={bridge}
    />
  );
}

export function renderPgStackCommitSequenceBarHtml(
  change?: GerritChange | null,
  idx = 0,
  totalCount = 0,
  activeStack?: GerritStack | null,
  bridge?: GerritBridgeAdapter
): string {
  return renderReactToHtml(
    <StackCommitSequenceBar
      change={change}
      idx={idx}
      totalCount={totalCount}
      activeStack={activeStack}
      bridge={bridge}
    />
  );
}
