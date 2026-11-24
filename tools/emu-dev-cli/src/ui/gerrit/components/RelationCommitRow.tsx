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
import { PATCHSET_BASE_TOKEN, PATCHSET_ZERO_TOKEN } from '../constants';
import { escapeHtml } from '../runtime';
import { isChangeAbandoned, isChangeMerged } from '../state/store';
import type {
  GerritBridgeAdapter,
  GerritChange,
  GerritRebaseStatus,
  RelatedChangesTabId,
} from '../types';
import { RelationStatusTag, RelationVoteChip } from './RelationStatusBadges';

export interface RelationCommitRowProps {
  peer: GerritChange;
  currentClNum: number;
  currentBranch: string;
  currentProject: string;
  mode: RelatedChangesTabId;
  bridge: GerritBridgeAdapter;
  stackId?: string;
  isFirst?: boolean;
  isLast?: boolean;
  rebaseStatus?: GerritRebaseStatus | null;
}

interface PatchsetChipState {
  peerTarget: number;
  peerBase: string;
  isPeerInterdiff: boolean;
  allPeerPs: number[];
  maxPeerPs: number;
  prevPeerPs: number;
}

const SINGLE_PATCHSET_LIST = [1];

function resolveSortedPatchsetNumbers(
  rawPatchSets: unknown,
  fallbackCount: number
): number[] {
  if (!Array.isArray(rawPatchSets) || rawPatchSets.length === 0) {
    if (fallbackCount <= 1) {
      return SINGLE_PATCHSET_LIST;
    }
    const seq: number[] = [];
    for (let i = 1; i <= fallbackCount; i++) {
      seq.push(i);
    }
    return seq;
  }
  if (rawPatchSets.length === 1) {
    const single = Number(rawPatchSets[0]);
    return single > 0 ? [single] : SINGLE_PATCHSET_LIST;
  }
  return Array.from(
    new Set(rawPatchSets.map(Number).filter((num) => num > 0))
  ).sort((a, b) => a - b);
}

function computePatchsetChipState(
  peer: GerritChange,
  pNum: number,
  bridge: GerritBridgeAdapter
): PatchsetChipState {
  const peerSel = bridge.getSelectedPatchset(pNum) || {};
  const peerBundle = bridge.getDiffBundle(pNum);
  const peerTarget = Number(
    peerSel.ps || peerBundle?.patch_set || peer.patch_set || 1
  );
  const peerBase = String(
    peerSel.base || peerBundle?.base_patch_set || PATCHSET_BASE_TOKEN
  );
  const isPeerInterdiff =
    peerBase !== PATCHSET_BASE_TOKEN && peerBase !== PATCHSET_ZERO_TOKEN;
  const fallbackCount = Math.max(1, Number(peerTarget || peer.patch_set || 1));
  const allPeerPs = resolveSortedPatchsetNumbers(peer.patch_sets, fallbackCount);
  const maxPeerPs = allPeerPs.length ? allPeerPs[allPeerPs.length - 1] : 1;
  const curPeerIdx = allPeerPs.indexOf(peerTarget);
  const prevPeerPs = curPeerIdx > 0 ? allPeerPs[curPeerIdx - 1] : peerTarget - 1;
  return {
    peerTarget,
    peerBase,
    isPeerInterdiff,
    allPeerPs,
    maxPeerPs,
    prevPeerPs,
  };
}

function resolveNodeDotClass(params: {
  isCurrent: boolean;
  isMerged: boolean;
  isAbandoned: boolean;
  isConflict: boolean;
  isPeerOutdated: boolean;
  isSubmittable: boolean;
  isWip: boolean;
}): string {
  if (params.isCurrent) return 'dot-current';
  if (params.isMerged) return 'dot-merged';
  if (params.isAbandoned) return 'dot-abandoned';
  if (params.isConflict) return 'dot-conflict';
  if (params.isPeerOutdated) return 'dot-outdated';
  if (params.isSubmittable) return 'dot-submittable';
  if (params.isWip) return 'dot-wip';
  return 'dot-open';
}

function resolveCommitClickAction(params: {
  mode: RelatedChangesTabId;
  pNum: number;
  isCurrent: boolean;
  isBatchMode: boolean;
  isContinuous: boolean;
  stackId?: string;
}): string {
  if (params.mode === 'cherrypicks' && params.isCurrent) {
    return '';
  }
  if (params.isBatchMode) {
    return `toggleClCheckbox(${params.pNum}, event)`;
  }
  if (params.mode === 'chain') {
    return params.isContinuous
      ? `scrollToStackCommit(${params.pNum})`
      : `selectStackAndCl('${escapeHtml(params.stackId || '')}', ${params.pNum})`;
  }
  return `selectCl(${params.pNum})`;
}

function ChainPatchsetControls({
  peer,
  pNum,
  bridge,
}: {
  peer: GerritChange;
  pNum: number;
  bridge: GerritBridgeAdapter;
}): React.ReactElement {
  const psState = computePatchsetChipState(peer, pNum, bridge);
  const { peerTarget, peerBase, isPeerInterdiff, allPeerPs, maxPeerPs, prevPeerPs } =
    psState;
  const latestSuffix = peerTarget === maxPeerPs ? ' (latest)' : '';

  return (
    <>
      {allPeerPs.length > 1 ? (
        <button
          type="button"
          className="pg-rel-ps-badge is-clickable"
          htmlOnClick={`event.stopPropagation(); stepPgPatchset(${pNum}, 1)`}
          title={`Patchset ${peerTarget} of ${maxPeerPs}${latestSuffix}. Click to cycle patchset (or [ / ] to step)`}
        >
          {`PS${peerTarget}`}
        </button>
      ) : (
        <span
          className="pg-rel-ps-badge"
          title={`Patchset ${peerTarget} of ${maxPeerPs}${latestSuffix} (ag/${pNum})`}
        >
          {`PS${peerTarget}`}
        </span>
      )}
      {peerTarget < maxPeerPs ? (
        <button
          type="button"
          id={`btnRelLatest-${pNum}`}
          className="pg-btn-subtle-rel pg-rel-latest-chip"
          htmlOnClick={`event.stopPropagation(); selectPgPatchsetComparison(${pNum}, ${maxPeerPs}, 'base')`}
          title={`Jump to latest patchset PS ${maxPeerPs} for ag/${pNum}`}
        >
          {`➔ PS${maxPeerPs} (latest)`}
        </button>
      ) : null}
      {isPeerInterdiff ? (
        <button
          type="button"
          id={`btnRelInterdiff-${pNum}`}
          className="pg-btn-subtle-rel pg-rel-interdiff-chip is-active"
          htmlOnClick={`event.stopPropagation(); resetPgPatchsetComparisonToBase(${pNum})`}
          title={`Comparing PS ${peerBase} → PS ${peerTarget} for ag/${pNum}. Click to reset to Base (Shortcut: b)`}
        >
          {`⚡ PS ${peerBase}→${peerTarget}`}
        </button>
      ) : allPeerPs.length > 1 && peerTarget > 1 ? (
        <button
          type="button"
          id={`btnRelInterdiff-${pNum}`}
          className="pg-btn-subtle-rel pg-rel-interdiff-chip"
          htmlOnClick={`event.stopPropagation(); togglePgDiffPreviousPatchset(${pNum})`}
          title={`Compare PS ${prevPeerPs} → PS ${peerTarget} for ag/${pNum} (Shortcut: b)`}
        >
          {`⚡ vs PS ${prevPeerPs}`}
        </button>
      ) : null}
    </>
  );
}

function ChainRebaseButton({
  pNum,
  peerBranch,
  isCurrent,
  isMerged,
  isAbandoned,
  rebaseStatus,
}: {
  pNum: number;
  peerBranch: string;
  isCurrent: boolean;
  isMerged: boolean;
  isAbandoned: boolean;
  rebaseStatus: GerritRebaseStatus | null;
}): React.ReactElement | null {
  if (isMerged || isAbandoned || !rebaseStatus) {
    return null;
  }
  if (!rebaseStatus.needsRebase && !isCurrent) {
    return null;
  }
  const parentArg = rebaseStatus.parentClNumber || 'null';
  const targetDesc = rebaseStatus.parentClNumber
    ? `on parent ag/${rebaseStatus.parentClNumber}`
    : `on ${peerBranch || 'base branch'}`;
  const btnCls = `pg-btn-subtle-rel pg-btn-rel-rebase${
    rebaseStatus.needsRebase ? ' needs-rebase' : ''
  }`;
  return (
    <button
      type="button"
      className={btnCls}
      htmlOnClick={`onPgRelationRebaseClick(event, ${pNum}, ${parentArg})`}
      title={`Rebase change ag/${pNum} ${targetDesc} (Alt+Click to rebase instantly)`}
    >
      ⚡ Rebase
    </button>
  );
}

export function RelationCommitRow({
  peer,
  currentClNum,
  currentBranch,
  currentProject,
  mode,
  bridge,
  stackId,
  isFirst = false,
  isLast = false,
  rebaseStatus: rebaseStatusProp,
}: RelationCommitRowProps): React.ReactElement {
  const pNum = Number(peer.number);
  const isCurrent = pNum === currentClNum;
  const isChainMode = mode === 'chain';
  const peerBranch = bridge.getBranchDisplay(peer.branch);
  const isCrossBranch = Boolean(
    peerBranch && currentBranch && peerBranch !== currentBranch
  );
  const peerProject = bridge.getProjectDisplay(peer.project);
  const isCrossProject = Boolean(
    peerProject && currentProject && peerProject !== currentProject
  );
  const rebaseStatus =
    rebaseStatusProp !== undefined
      ? rebaseStatusProp
      : isChainMode
        ? bridge.getChangeRebaseStatus(peer)
        : null;
  const isPeerOutdated = Boolean(isChainMode && rebaseStatus?.isOutdated);
  const isMerged = isChangeMerged(peer);
  const isAbandoned = isChangeAbandoned(peer);
  const isConflict = bridge.isClConflict(peer);
  const isSubmittable = bridge.isClSubmittable(peer);
  const isChecked = bridge.isClChecked(pNum);
  const isBatchMode = bridge.isBatchModeActive();

  const nodeClass = resolveNodeDotClass({
    isCurrent,
    isMerged,
    isAbandoned,
    isConflict,
    isPeerOutdated,
    isSubmittable,
    isWip: Boolean(peer.work_in_progress),
  });

  const itemClasses =
    'pg-relation-item' +
    (isCurrent ? ' is-current' : '') +
    (isMerged ? ' is-merged' : '') +
    (isAbandoned ? ' is-abandoned' : '') +
    (isConflict ? ' has-conflict' : '') +
    (isPeerOutdated ? ' is-outdated' : '') +
    (isChecked ? ' is-checked' : '');

  const clickAction = resolveCommitClickAction({
    mode,
    pNum,
    isCurrent,
    isBatchMode,
    isContinuous: bridge.isContinuousStackDiff(),
    stackId,
  });

  const keyDownAction = isChainMode
    ? `onPgRelationItemKeyDown(event, ${pNum}, '${escapeHtml(stackId || '')}')`
    : `onPgRelationItemKeyDown(event, ${pNum})`;

  const treeColClass = isChainMode
    ? 'pg-rel-tree-col' + (isFirst ? ' is-first' : '') + (isLast ? ' is-last' : '')
    : 'pg-rel-tree-col';

  const subjectText = peer.subject || '';

  return (
    <div
      className={itemClasses}
      tabIndex={0}
      role="link"
      aria-label={`${isCurrent ? 'Current change: ' : ''}ag/${pNum}: ${subjectText}`}
      data-pnum={pNum}
      htmlOnClick={clickAction}
      htmlOnKeyDown={keyDownAction}
      title={`ag/${pNum}: ${subjectText}`}
    >
      <span className="pg-relation-arrow" aria-hidden="true">
        {isCurrent ? '➔' : ''}
      </span>
      <div className={treeColClass}>
        {isChainMode ? <div className="pg-rel-tree-line"></div> : null}
        <div className={`pg-rel-node-dot ${nodeClass}`}></div>
      </div>
      <div className="pg-rel-item-body">
        <div className="pg-rel-item-meta">
          {isChecked ? (
            <span
              className="pg-rel-checked-badge"
              title="Selected for Batch Edit (Press x to toggle)"
            >
              ☑
            </span>
          ) : null}
          <span className="pg-rel-cl-num">{`ag/${pNum}`}</span>
          {isChainMode ? (
            <ChainPatchsetControls peer={peer} pNum={pNum} bridge={bridge} />
          ) : null}
          {peerBranch ? (
            <span
              className={`pg-rel-branch-badge${
                isCrossBranch ? ' is-cross-branch' : ''
              }`}
              title={`Branch: ${peer.branch || ''}${
                isCrossBranch ? ' (different branch)' : ''
              }`}
            >
              {peerBranch}
            </span>
          ) : null}
          {isCrossProject ? (
            <span
              className="pg-rel-project-badge"
              title={`Project: ${peer.project || ''}`}
            >
              {peerProject}
            </span>
          ) : null}
          <RelationStatusTag
            peer={peer}
            bridge={bridge}
            isConflict={isConflict}
            rebaseStatus={rebaseStatus}
          />
          <RelationVoteChip peer={peer} bridge={bridge} />
          {isSubmittable ? (
            <span className="pg-relation-submittable" title="Ready to submit">
              ✓
            </span>
          ) : null}
          {isChainMode ? (
            <ChainRebaseButton
              pNum={pNum}
              peerBranch={peerBranch}
              isCurrent={isCurrent}
              isMerged={isMerged}
              isAbandoned={isAbandoned}
              rebaseStatus={rebaseStatus}
            />
          ) : null}
        </div>
        <span className="pg-relation-subj">{subjectText}</span>
      </div>
    </div>
  );
}
