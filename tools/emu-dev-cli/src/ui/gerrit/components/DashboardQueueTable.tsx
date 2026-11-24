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
import {
  CODE_REVIEW_DISLIKE_VOTE,
  CODE_REVIEW_LIKE_VOTE,
  CODE_REVIEW_REJECT_VOTE,
  DEFAULT_AGENT_REQUIRED_CR2,
  DEFAULT_HUMAN_REQUIRED_CR2,
} from '../constants';
import { escapeHtml, renderReactToHtml } from '../runtime';
import type { GerritChange, GerritReviewer } from '../types';

export interface SubmitButtonTarget {
  canSubmit: boolean;
  title: string;
  label: string;
}

export interface VerifiedVoteDisplay {
  score: number;
  text: string;
  cls: string;
  tooltip: string;
}

export interface PresubmitVoteDisplay {
  status: string;
  tooltip: string;
}

export interface QueueFilterSummary {
  filterActive?: boolean;
  autoBypassedGates?: boolean;
  gatedCommitCount?: number;
  ungatedCommitCount?: number;
  hiddenByGatesCount?: number;
}

export interface DashboardStatusFlags {
  isMerged: boolean;
  isAbandoned: boolean;
  hasConflict: boolean;
  isWip: boolean;
  isCcOnly: boolean;
  isSubmittable: boolean;
}

export function CommitSubmitButton({
  target,
  isBusy = false,
}: {
  target: SubmitButtonTarget;
  isBusy?: boolean;
}): React.ReactElement {
  const isDisabled = !target.canSubmit || isBusy;
  return (
    <button
      id="btnSubmitCl"
      type="button"
      className="pg-btn-primary btn-submit-cl"
      htmlStyle={target.canSubmit ? undefined : 'display:none;'}
      disabled={isDisabled}
      aria-disabled={isDisabled ? 'true' : 'false'}
      htmlOnClick="event.stopPropagation(); submitActiveClOrTopic(this)"
      title={target.title}
    >
      {target.label}
    </button>
  );
}

export function BatchSubmitButton({
  target,
  isBusy = false,
}: {
  target: SubmitButtonTarget;
  isBusy?: boolean;
}): React.ReactElement {
  const isDisabled = !target.canSubmit || isBusy;
  return (
    <button
      id="btnBatchSubmitCls"
      type="button"
      className="pg-btn-primary btn btn-submit-cl pg-batch-btn-submit"
      disabled={isDisabled}
      aria-disabled={isDisabled ? 'true' : 'false'}
      htmlOnClick="event.stopPropagation(); submitBatchCheckedCls(this)"
      title={target.title}
    >
      {target.label}
    </button>
  );
}

interface CrVoterObject {
  short?: string;
  name?: string;
  email?: string;
}

function formatCr2WhoList(rawHumanCr2: unknown[]): string {
  return rawHumanCr2
    .map((item) => {
      if (typeof item === 'string') {
        return item;
      }
      if (item && typeof item === 'object') {
        const v = item as CrVoterObject;
        return v.short || v.name || v.email || '';
      }
      return '';
    })
    .filter(Boolean)
    .join(', ');
}

export function CrVoteCell({
  change,
  reviewers = [],
}: {
  change: GerritChange;
  reviewers?: GerritReviewer[];
}): React.ReactElement {
  const humanRevs = reviewers.filter((p) => !p.is_bot);
  let minVote = 0;
  for (const rev of humanRevs) {
    const v = Number(rev.cr_vote || 0);
    if (v < minVote) {
      minVote = v;
    }
  }
  if (minVote <= CODE_REVIEW_REJECT_VOTE) {
    return (
      <span
        className="pg-vote-chip v-rej"
        title="Code-Review -2 (Do not submit)"
      >
        -2
      </span>
    );
  }
  if (minVote === CODE_REVIEW_DISLIKE_VOTE) {
    return (
      <span className="pg-vote-chip v-disliked" title="Code-Review -1">
        -1
      </span>
    );
  }
  const reqCount = Number(
    change.required_cr2_count ||
      (change.is_agent_cl
        ? DEFAULT_AGENT_REQUIRED_CR2
        : DEFAULT_HUMAN_REQUIRED_CR2)
  );
  const humanCr2 = Array.isArray(change.human_cr2) ? change.human_cr2 : [];
  const doneCount = humanCr2.length || Number(change.human_cr2_count || 0);
  if (doneCount >= reqCount && reqCount > 0) {
    const who = formatCr2WhoList(humanCr2);
    const tip = who
      ? `Code-Review +2 satisfied (${who})`
      : `Code-Review +2 satisfied (${doneCount}/${reqCount})`;
    return (
      <span className="pg-vote-chip v-approved" title={tip}>
        ✓+2
      </span>
    );
  }
  if (doneCount > 0) {
    return (
      <span
        className="pg-vote-chip v-rec"
        title={`Code-Review: ${doneCount}/${reqCount} human +2 approvals (needs 1 more +2)`}
      >
        +2·½
      </span>
    );
  }
  const hasCr1 =
    (Array.isArray(change.human_cr1) && change.human_cr1.length > 0) ||
    humanRevs.some((p) => Number(p.cr_vote || 0) === CODE_REVIEW_LIKE_VOTE);
  if (hasCr1) {
    return (
      <span
        className="pg-vote-chip v-rec"
        title={`Code-Review +1 (needs +${DEFAULT_AGENT_REQUIRED_CR2})`}
      >
        +1
      </span>
    );
  }
  const needTip =
    reqCount >= DEFAULT_AGENT_REQUIRED_CR2
      ? 'Code-Review: Needs 2 human +2 approvals (agent CL)'
      : 'Code-Review: Needs +2 approval';
  return (
    <span className="pg-vote-empty" title={needTip}>
      —
    </span>
  );
}

export function VVoteBody({
  display,
}: {
  display: VerifiedVoteDisplay;
}): React.ReactElement {
  return (
    <span className={display.cls} title={display.tooltip}>
      {display.text}
    </span>
  );
}

export function VVoteCell({
  display,
}: {
  display: VerifiedVoteDisplay;
}): React.ReactElement {
  return (
    <span className="pg-v-chip-wrap" title={display.tooltip}>
      <VVoteBody display={display} />
    </span>
  );
}

export function PvVoteBody({
  display,
}: {
  display: PresubmitVoteDisplay;
}): React.ReactElement {
  if (display.status === 'passed') {
    return (
      <span className="pg-vote-chip v-approved" title={display.tooltip}>
        ✓
      </span>
    );
  }
  if (display.status === 'warning') {
    return (
      <span className="pg-vote-chip v-rec" title={display.tooltip}>
        ⚠+1
      </span>
    );
  }
  if (display.status === 'failed') {
    return (
      <span className="pg-vote-chip v-rej" title={display.tooltip}>
        ✗
      </span>
    );
  }
  if (display.status === 'running' || display.status === 'queued') {
    return (
      <span
        className="pg-vote-chip"
        htmlStyle="color:var(--g-accent);"
        title={display.tooltip}
      >
        <span className="pv-mini-spinner" />
      </span>
    );
  }
  return (
    <span className="pg-vote-empty" title={display.tooltip}>
      —
    </span>
  );
}

export function PvVoteCell({
  clNumber,
  stackId = '',
  display,
}: {
  clNumber: number;
  stackId?: string;
  display: PresubmitVoteDisplay;
}): React.ReactElement {
  const escapedStackId = escapeHtml(stackId || '');
  return (
    <button
      type="button"
      className="pg-pv-cell-btn"
      data-pv-cl={clNumber}
      htmlOnClick={`event.stopPropagation(); openPresubmitTargetsFromCard('${escapedStackId}', ${clNumber})`}
      title={display.tooltip}
    >
      <PvVoteBody display={display} />
    </button>
  );
}

export function DashboardStatusTag({
  flags,
}: {
  flags: DashboardStatusFlags;
}): React.ReactElement | null {
  if (flags.isMerged) {
    return (
      <span className="pg-mini-status st-merged" title="Merged">
        <span className="pg-status-full">Merged</span>
        <span className="pg-status-mini">Merged</span>
      </span>
    );
  }
  if (flags.isAbandoned) {
    return (
      <span className="pg-mini-status st-abandoned" title="Abandoned">
        <span className="pg-status-full">Abandoned</span>
        <span className="pg-status-mini">Aband.</span>
      </span>
    );
  }
  if (flags.hasConflict) {
    return (
      <>
        <span
          className="pg-mini-status st-conflict"
          title="Merge Conflict — rebase required"
        >
          <span className="pg-status-full">Merge Conflict</span>
          <span className="pg-status-mini">Conflict</span>
        </span>
        {flags.isWip ? (
          <>
            {' '}
            <span className="pg-mini-status st-wip" title="Work in Progress">
              <span className="pg-status-full">WIP</span>
              <span className="pg-status-mini">WIP</span>
            </span>
          </>
        ) : null}
      </>
    );
  }
  if (flags.isWip) {
    return (
      <span className="pg-mini-status st-wip" title="Work in Progress">
        <span className="pg-status-full">WIP</span>
        <span className="pg-status-mini">WIP</span>
      </span>
    );
  }
  if (flags.isCcOnly) {
    return (
      <span
        className="pg-mini-status st-cc"
        title="You are CCed on this change"
      >
        <span className="pg-status-full">CC</span>
        <span className="pg-status-mini">CC</span>
      </span>
    );
  }
  if (flags.isSubmittable) {
    return (
      <span
        className="pg-mini-status st-ready"
        title="Submittable — all approvals & presubmits satisfied"
      >
        <span className="pg-status-full">Ready</span>
        <span className="pg-status-mini">Ready</span>
      </span>
    );
  }
  return null;
}

export function GroupCheckbox({
  nums,
  checkedCount,
  labelTitle,
}: {
  nums: number[];
  checkedCount: number;
  labelTitle?: string;
}): React.ReactElement {
  const cleanNums = (nums || []).map(Number).filter((n) => n > 0);
  const csv = cleanNums.join(',');
  const allChecked = cleanNums.length > 0 && checkedCount === cleanNums.length;
  const tip = labelTitle || 'Select or deselect all commits in this group';
  return (
    <label
      className="group-check-wrap"
      htmlOnClick="event.stopPropagation()"
      title={tip}
    >
      <input
        type="checkbox"
        className="group-select-checkbox"
        data-group-cls={csv}
        checked={allChecked}
        htmlOnClick={`toggleGroupCheckbox([${csv}], event)`}
        aria-label={tip}
      />
    </label>
  );
}

export function ColHeaderRow({
  groupNums,
  checkedCount,
  groupTitle,
  whoLabel = 'Owner',
  ageLabel = 'Updated',
}: {
  groupNums: number[];
  checkedCount: number;
  groupTitle?: string;
  whoLabel?: string;
  ageLabel?: string;
}): React.ReactElement {
  const cleanNums = (groupNums || []).map(Number).filter((n) => n > 0);
  const hasSel = checkedCount > 0;
  return (
    <div className={`pg-col-header-row${hasSel ? ' has-selection' : ''}`}>
      <span htmlStyle="display:flex;align-items:center;justify-content:center;">
        {cleanNums.length > 0 ? (
          <GroupCheckbox
            nums={cleanNums}
            checkedCount={checkedCount}
            labelTitle={groupTitle}
          />
        ) : null}
      </span>
      <span>Subject</span>
      <span className="pg-col-who">{whoLabel}</span>
      <span className="pg-col-age pg-col-label-right">{ageLabel}</span>
      <span className="pg-col-size pg-col-label-center">Size</span>
      <span className="pg-col-label-center" title="Code-Review">
        CR
      </span>
      <span className="pg-col-label-center pg-col-v" title="Verified">
        V
      </span>
      <span className="pg-col-label-center" title="Presubmit-Verified">
        PV
      </span>
    </div>
  );
}

export function QueueBatchIdleHint(): React.ReactElement {
  return (
    <>
      <span className="queue-batch-hint-full">
        Check commits for batch actions
      </span>
      <span className="queue-batch-hint-compact">Batch edit</span>
    </>
  );
}

export function QueueBatchSelectStrip({
  cleanNums,
  checkedInGroupCount,
  totalChecked,
}: {
  cleanNums: number[];
  checkedInGroupCount: number;
  totalChecked: number;
}): React.ReactElement | null {
  if (!cleanNums || cleanNums.length === 0) {
    return null;
  }
  const stripCls =
    totalChecked > 0 ? 'queue-batch-strip has-checked' : 'queue-batch-strip';
  return (
    <div id="queueBatchStrip" className={stripCls}>
      <span className="queue-batch-left">
        <GroupCheckbox
          nums={cleanNums}
          checkedCount={checkedInGroupCount}
          labelTitle={`Select all ${cleanNums.length} visible commits`}
        />
        <span>{`Select All (${cleanNums.length})`}</span>
      </span>
      {totalChecked > 0 ? (
        <span id="queueBatchSelectMeta" className="queue-checked-pill">
          <span>{`${totalChecked} selected`}</span>
          <button
            type="button"
            className="pg-btn-flat pg-batch-btn-clear nudge-btn"
            htmlOnClick="event.stopPropagation(); clearCheckedCls()"
            title="Clear checked selections"
          >
            Clear ✕
          </button>
        </span>
      ) : (
        <span id="queueBatchSelectMeta" htmlStyle="color:var(--g-subtle);">
          <QueueBatchIdleHint />
        </span>
      )}
    </div>
  );
}

export function QueueFilterBanner({
  summary,
  authorFilter = 'all',
  searchQuery = '',
  includeUngatedMatches = false,
}: {
  summary?: QueueFilterSummary | null;
  authorFilter?: string;
  searchQuery?: string;
  includeUngatedMatches?: boolean;
}): React.ReactElement | null {
  if (!summary || !summary.filterActive) {
    return null;
  }
  const activeDesc: string[] = [];
  if (authorFilter && authorFilter !== 'all') {
    activeDesc.push(`author:${authorFilter}`);
  }
  const trimmedSearch = (searchQuery || '').trim();
  if (trimmedSearch) {
    activeDesc.push(`"${trimmedSearch}"`);
  }
  const labelStr = activeDesc.join(' · ');
  const gatedCount = Number(summary.gatedCommitCount || 0);
  const ungatedCount = Number(summary.ungatedCommitCount || 0);
  const hiddenCount = Number(summary.hiddenByGatesCount || 0);

  if (summary.autoBypassedGates && gatedCount === 0 && ungatedCount > 0) {
    return (
      <div className="queue-filter-banner">
        <span>
          {'ℹ️ Showing '}
          <strong>{ungatedCount}</strong>
          {` matching CL${ungatedCount === 1 ? '' : 's'} for `}
          <strong>{labelStr}</strong>
          {" (including unverified / already +2'd CLs)."}
        </span>
        <button
          className="nudge-btn"
          htmlOnClick="clearQueueSearchAndAuthor()"
        >
          Clear Filter ✕
        </button>
      </div>
    );
  }
  if (hiddenCount > 0 && !includeUngatedMatches) {
    return (
      <div className="queue-filter-banner">
        <span>
          {'🔍 Showing '}
          <strong>{gatedCount}</strong>
          {` verified CL${gatedCount === 1 ? '' : 's'} for `}
          <strong>{labelStr}</strong>
          {' · '}
          <strong>{`+${hiddenCount} more`}</strong>
          {' hidden by active gates.'}
        </span>
        <div htmlStyle="display:flex;gap:6px;">
          <button
            className="nudge-btn"
            htmlOnClick="toggleIncludeUngatedMatches()"
          >
            {`Show All (${ungatedCount})`}
          </button>
          <button
            className="nudge-btn"
            htmlOnClick="clearQueueSearchAndAuthor()"
          >
            Clear ✕
          </button>
        </div>
      </div>
    );
  }
  if (includeUngatedMatches && hiddenCount > 0) {
    return (
      <div className="queue-filter-banner">
        <span>
          {'🔍 Showing all '}
          <strong>{ungatedCount}</strong>
          {' matching CLs for '}
          <strong>{labelStr}</strong>
          {` (including ${hiddenCount} ungated).`}
        </span>
        <div htmlStyle="display:flex;gap:6px;">
          <button
            className="nudge-btn"
            htmlOnClick="toggleIncludeUngatedMatches()"
          >
            {`Verified Only (${gatedCount})`}
          </button>
          <button
            className="nudge-btn"
            htmlOnClick="clearQueueSearchAndAuthor()"
          >
            Clear ✕
          </button>
        </div>
      </div>
    );
  }
  return null;
}

export function SidebarHydratingPill({
  hydrating = false,
}: {
  hydrating?: boolean;
}): React.ReactElement | null {
  if (!hydrating) {
    return null;
  }
  return (
    <div className="sidebar-hydrating-pill">
      <span className="pv-mini-spinner" />
      <span>Loading remaining commits in background…</span>
    </div>
  );
}

export function renderCommitSubmitButtonHtml(
  target: SubmitButtonTarget,
  isBusy = false
): string {
  return renderReactToHtml(
    <CommitSubmitButton target={target} isBusy={isBusy} />
  );
}

export function renderBatchSubmitButtonHtml(
  target: SubmitButtonTarget,
  isBusy = false
): string {
  return renderReactToHtml(
    <BatchSubmitButton target={target} isBusy={isBusy} />
  );
}

export function renderPgCrVoteCellHtml(
  change: GerritChange,
  reviewers: GerritReviewer[] = []
): string {
  return renderReactToHtml(
    <CrVoteCell change={change} reviewers={reviewers} />
  );
}

export function renderPgVVoteBodyHtml(display: VerifiedVoteDisplay): string {
  return renderReactToHtml(<VVoteBody display={display} />);
}

export function renderPgVVoteCellHtml(display: VerifiedVoteDisplay): string {
  return renderReactToHtml(<VVoteCell display={display} />);
}

export function renderPgPvVoteBodyHtml(display: PresubmitVoteDisplay): string {
  return renderReactToHtml(<PvVoteBody display={display} />);
}

export function renderPgPvVoteCellHtml(
  clNumber: number,
  stackId: string,
  display: PresubmitVoteDisplay
): string {
  return renderReactToHtml(
    <PvVoteCell clNumber={clNumber} stackId={stackId} display={display} />
  );
}

export function renderPgStatusTagHtml(flags: DashboardStatusFlags): string {
  return renderReactToHtml(<DashboardStatusTag flags={flags} />);
}

export function renderGroupCheckboxHtml(
  nums: number[],
  checkedCount: number,
  labelTitle?: string
): string {
  return renderReactToHtml(
    <GroupCheckbox
      nums={nums}
      checkedCount={checkedCount}
      labelTitle={labelTitle}
    />
  );
}

export function renderPgColHeaderRowHtml(
  groupNums: number[],
  checkedCount: number,
  groupTitle?: string,
  whoLabel = 'Owner',
  ageLabel = 'Updated'
): string {
  return renderReactToHtml(
    <ColHeaderRow
      groupNums={groupNums}
      checkedCount={checkedCount}
      groupTitle={groupTitle}
      whoLabel={whoLabel}
      ageLabel={ageLabel}
    />
  );
}

export function buildQueueBatchIdleHintHtml(): string {
  return renderReactToHtml(<QueueBatchIdleHint />);
}

export function renderQueueBatchSelectStripHtml(
  cleanNums: number[],
  checkedInGroupCount: number,
  totalChecked: number
): string {
  return renderReactToHtml(
    <QueueBatchSelectStrip
      cleanNums={cleanNums}
      checkedInGroupCount={checkedInGroupCount}
      totalChecked={totalChecked}
    />
  );
}

export function renderQueueFilterBannerHtml(
  summary: QueueFilterSummary | null | undefined,
  authorFilter = 'all',
  searchQuery = '',
  includeUngatedMatches = false
): string {
  return renderReactToHtml(
    <QueueFilterBanner
      summary={summary}
      authorFilter={authorFilter}
      searchQuery={searchQuery}
      includeUngatedMatches={includeUngatedMatches}
    />
  );
}

export function renderSidebarHydratingPillHtml(hydrating = false): string {
  return renderReactToHtml(<SidebarHydratingPill hydrating={hydrating} />);
}
