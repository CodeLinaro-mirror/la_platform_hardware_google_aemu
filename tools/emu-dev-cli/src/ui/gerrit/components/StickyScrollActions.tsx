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
import { renderReactToHtml } from '../runtime';

export interface StickyScrollActionsProps {
  clNum: number;
  draftCount?: number;
}

export function StickyScrollActions({
  clNum,
  draftCount = 0,
}: StickyScrollActionsProps): React.ReactElement {
  const num = Number(clNum);
  const count = Math.max(0, Number(draftCount || 0));
  const replyBtnLabel =
    count > 0 ? `Reply (${count} draft${count > 1 ? 's' : ''})` : 'Reply';
  const replyClass = `pg-btn-primary pg-reply-btn pg-sticky-scroll-only-btn btn-header-reply${
    count > 0 ? ' has-drafts' : ''
  }`;

  return (
    <>
      <button
        id={`btnStickyHeaderReply-${num}`}
        type="button"
        className={replyClass}
        htmlOnClick={`event.stopPropagation(); openPgHeaderReply(${num})`}
        title={`Reply and vote on ag/${num} (Shortcut: r)`}
      >
        {replyBtnLabel}
      </button>
      <button
        id={`btnStickyBackToTop-${num}`}
        type="button"
        className="btn pg-btn-header-action pg-sticky-scroll-only-btn btn-back-to-top"
        htmlOnClick={`event.stopPropagation(); scrollPgChangeToTop(${num})`}
        title={`Scroll back to top of change ag/${num}`}
      >
        <span className="pg-btn-icon" aria-hidden="true">
          ↑
        </span>
        {' Top'}
      </button>
    </>
  );
}

export function renderPgStickyScrollActionsHtml(
  clNum: number,
  draftCount = 0
): string {
  return renderReactToHtml(
    <StickyScrollActions clNum={clNum} draftCount={draftCount} />
  );
}
