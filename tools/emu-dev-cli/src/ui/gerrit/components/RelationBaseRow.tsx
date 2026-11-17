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
import { DEFAULT_BRANCH_NAME, SHORT_SHA_LENGTH } from '../constants';
import { escapeHtml, renderReactToHtml } from '../runtime';
import { createHostBridge, isChangeMerged } from '../state/store';
import type { GerritBridgeAdapter, GerritChange, GerritStack } from '../types';

export interface RelationBaseRowProps {
  change?: GerritChange | null;
  stack?: GerritStack | null;
  bridge?: GerritBridgeAdapter;
}

function resolveFirstParentEntrySha(baseCommit?: GerritChange | null): string {
  if (!baseCommit || !Array.isArray(baseCommit.parents) || !baseCommit.parents[0]) {
    return '';
  }
  const firstParent = baseCommit.parents[0];
  if (typeof firstParent === 'string') {
    return firstParent;
  }
  return String(firstParent.commit || firstParent.sha || '');
}

function resolveParentCommitSha(
  change: GerritChange,
  baseCommit?: GerritChange | null
): string {
  const rawSha =
    baseCommit?.parent_revision ||
    baseCommit?.parent_commit ||
    resolveFirstParentEntrySha(baseCommit) ||
    change.parent_revision ||
    change.parent_commit ||
    '';
  return String(rawSha).trim();
}

export function RelationBaseRow({
  change,
  stack,
  bridge,
}: RelationBaseRowProps): React.ReactElement | null {
  if (!change || !stack || !Array.isArray(stack.commits) || stack.commits.length === 0) {
    return null;
  }
  const activeBridge = bridge ?? createHostBridge();
  const baseCommit = stack.commits[0];
  const targetBranch = String(
    baseCommit?.branch || change.branch || DEFAULT_BRANCH_NAME
  ).trim();
  const branchDisplay = activeBridge.getBranchDisplay(targetBranch);
  const parentSha = resolveParentCommitSha(change, baseCommit);
  const shortSha = parentSha ? parentSha.slice(0, SHORT_SHA_LENGTH) : '';
  const isBaseMerged = isChangeMerged(baseCommit) || isChangeMerged(change);
  const escapedBranch = escapeHtml(targetBranch);
  const escapedSha = escapeHtml(parentSha);
  const ariaLabel = `Target branch: ${targetBranch}${
    shortSha ? ` (Base: ${shortSha})` : ''
  }`;
  const descText = isBaseMerged
    ? `Merged into ${branchDisplay}`
    : `Base target branch: ${branchDisplay}${shortSha ? ` (${shortSha})` : ''}`;

  return (
    <div
      className="pg-relation-item pg-rel-base-row"
      tabIndex={0}
      role="link"
      aria-label={ariaLabel}
      htmlOnClick={`filterDashboardByBranch('${escapedBranch}')`}
      htmlOnKeyDown={`onPgRelationBaseItemKeyDown(event, '${escapedBranch}', '${escapedSha}')`}
      title={`Target branch: ${targetBranch} · Click to filter dashboard`}
    >
      <span className="pg-relation-arrow" aria-hidden="true"></span>
      <div className="pg-rel-tree-col is-base is-last">
        <div className="pg-rel-tree-line"></div>
        <div
          className="pg-rel-node-dot dot-base"
          title={`Target branch: ${targetBranch}`}
        ></div>
      </div>
      <div className="pg-rel-item-body">
        <div className="pg-rel-item-meta">
          <span className="pg-rel-base-tag">BASE</span>
          <span
            className="pg-rel-branch-badge is-base-branch"
            title={`Destination branch: ${targetBranch}`}
          >
            {branchDisplay}
          </span>
          {shortSha ? (
            <span className="pg-rel-base-sha" title={`Parent commit: ${parentSha}`}>
              <code>{shortSha}</code>
            </span>
          ) : null}
          {isBaseMerged ? (
            <span
              className="pg-status-tag merged"
              title={`Change or base merged into ${branchDisplay}`}
            >
              MERGED
            </span>
          ) : null}
        </div>
        <div className="pg-rel-base-sub">
          <span className="pg-relation-subj pg-rel-base-desc">{descText}</span>
          <div className="pg-rel-base-actions">
            <button
              type="button"
              id={`btnFilterBranch-${change.number}`}
              className="pg-btn-subtle-rel pg-btn-filter-branch"
              htmlOnClick={`event.stopPropagation(); filterDashboardByBranch('${escapedBranch}')`}
              title={`Filter dashboard by branch '${targetBranch}'`}
            >
              🔍 Branch
            </button>
            <button
              type="button"
              id={`btnCopyBranch-${change.number}`}
              className="pg-btn-subtle-rel pg-btn-copy-branch"
              htmlOnClick={`event.stopPropagation(); copyPgBranch('${escapedBranch}')`}
              title={`Copy branch name '${targetBranch}'`}
            >
              📋 Branch
            </button>
            {parentSha ? (
              <button
                type="button"
                id={`btnCopyBaseSha-${change.number}`}
                className="pg-btn-subtle-rel pg-btn-copy-base-sha"
                htmlOnClick={`event.stopPropagation(); copyPgBaseCommitSha('${escapedSha}')`}
                title={`Copy base parent commit SHA '${parentSha}'`}
              >
                📋 Base SHA
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

export function renderPgRelBaseRowHtml(
  change?: GerritChange | null,
  stack?: GerritStack | null,
  bridge?: GerritBridgeAdapter
): string {
  return renderReactToHtml(
    <RelationBaseRow change={change} stack={stack} bridge={bridge} />
  );
}
