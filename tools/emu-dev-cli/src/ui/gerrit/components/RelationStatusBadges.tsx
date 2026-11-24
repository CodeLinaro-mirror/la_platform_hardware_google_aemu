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
import { renderReactToHtml } from '../runtime';
import {
  createHostBridge,
  isChangeAbandoned,
  isChangeMerged,
} from '../state/store';
import type {
  GerritBridgeAdapter,
  GerritChange,
  GerritRebaseStatus,
  GerritReviewer,
} from '../types';

export interface RelationStatusBadgeProps {
  peer?: GerritChange | null;
  bridge?: GerritBridgeAdapter;
  isConflict?: boolean;
  rebaseStatus?: GerritRebaseStatus | null;
}

const DEFAULT_OUTDATED_REASON =
  'Change is based on an older patchset of its parent — rebase recommended';

function summarizeHumanReviewerVotes(reviewers: GerritReviewer[]): {
  minVote: number;
  hasCr1: boolean;
} {
  let minVote = 0;
  let hasCr1 = false;
  for (let i = 0; i < reviewers.length; i++) {
    const reviewer = reviewers[i];
    if (!reviewer || reviewer.is_bot) continue;
    const vote = Number(reviewer.cr_vote || 0);
    if (vote < minVote) {
      minVote = vote;
    }
    if (vote === CODE_REVIEW_LIKE_VOTE) {
      hasCr1 = true;
    }
  }
  return { minVote, hasCr1 };
}

export function RelationStatusTag({
  peer,
  bridge,
  isConflict: isConflictProp,
  rebaseStatus: rebaseStatusProp,
}: RelationStatusBadgeProps): React.ReactElement | null {
  if (!peer) {
    return null;
  }
  const activeBridge = bridge ?? createHostBridge();
  if (isChangeMerged(peer)) {
    return (
      <span
        className="pg-status-tag merged"
        title="Change has merged into target branch"
      >
        MERGED
      </span>
    );
  }
  if (isChangeAbandoned(peer)) {
    return (
      <span className="pg-status-tag abandoned" title="Change is abandoned">
        ABANDONED
      </span>
    );
  }
  const hasConflict =
    isConflictProp !== undefined ? isConflictProp : activeBridge.isClConflict(peer);
  if (hasConflict) {
    return (
      <span
        className="pg-status-tag conflict"
        title="Merge conflict with target branch — rebase required"
      >
        CONFLICT
      </span>
    );
  }
  const rebaseStatus =
    rebaseStatusProp !== undefined
      ? rebaseStatusProp
      : activeBridge.getChangeRebaseStatus(peer);
  if (rebaseStatus && rebaseStatus.isOutdated) {
    const tagText = rebaseStatus.isIndirectlyOutdated ? 'INDIRECT' : 'NOT CURRENT';
    const tooltip = rebaseStatus.reason || DEFAULT_OUTDATED_REASON;
    return (
      <span className="pg-status-tag not-current" title={tooltip}>
        {tagText}
      </span>
    );
  }
  if (peer.work_in_progress) {
    return (
      <span className="pg-status-tag wip" title="Work in progress">
        WIP
      </span>
    );
  }
  return null;
}

export function RelationVoteChip({
  peer,
  bridge,
}: RelationStatusBadgeProps): React.ReactElement | null {
  if (!peer) {
    return null;
  }
  const activeBridge = bridge ?? createHostBridge();
  const reviewers = activeBridge.getFallbackReviewersForCl(peer);
  const { minVote, hasCr1: hasReviewerCr1 } = summarizeHumanReviewerVotes(reviewers);
  if (minVote <= CODE_REVIEW_REJECT_VOTE) {
    return (
      <span className="pg-vote-chip v-rej" title="Code-Review -2 (Do not submit)">
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
  const requiredCr2Count = Number(
    peer.required_cr2_count ||
      (peer.is_agent_cl ? DEFAULT_AGENT_REQUIRED_CR2 : DEFAULT_HUMAN_REQUIRED_CR2)
  );
  const humanCr2List = Array.isArray(peer.human_cr2) ? peer.human_cr2 : [];
  const doneCount = humanCr2List.length || Number(peer.human_cr2_count || 0);
  if (doneCount >= requiredCr2Count && requiredCr2Count > 0) {
    return (
      <span className="pg-vote-chip v-approved" title="Code-Review +2 satisfied">
        ✓+2
      </span>
    );
  }
  if (doneCount > 0) {
    return (
      <span
        className="pg-vote-chip v-rec"
        title={`Code-Review: ${doneCount}/${requiredCr2Count} human +2 approvals`}
      >
        +2·½
      </span>
    );
  }
  const hasCr1 =
    (Array.isArray(peer.human_cr1) && peer.human_cr1.length > 0) || hasReviewerCr1;
  if (hasCr1) {
    return (
      <span className="pg-vote-chip v-rec" title="Code-Review +1">
        +1
      </span>
    );
  }
  return null;
}

export function renderPgRelStatusTag(
  peer?: GerritChange | null,
  bridge?: GerritBridgeAdapter
): string {
  return renderReactToHtml(<RelationStatusTag peer={peer} bridge={bridge} />);
}

export function renderPgRelVoteChip(
  peer?: GerritChange | null,
  bridge?: GerritBridgeAdapter
): string {
  return renderReactToHtml(<RelationVoteChip peer={peer} bridge={bridge} />);
}
