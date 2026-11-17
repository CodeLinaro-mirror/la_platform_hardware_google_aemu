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
import { isChangeAbandoned, isChangeMerged } from '../state/store';
import type {
  GerritChange,
  GerritChangeReferenceInfo,
  GerritDiffBundle,
  GerritHeaderStatusInfo,
  GerritSrSummary,
} from '../types';

const BTN_REVERT_PREFIX = 'btnHeaderRevert-';
const BTN_MARK_READY_PREFIX = 'btnHeaderMarkReady-';
const BTN_MARK_WIP_PREFIX = 'btnHeaderMarkWip-';
const BTN_PUBLISH_EDIT_PREFIX = 'btnPublishEdit-';
const BTN_DISCARD_EDIT_PREFIX = 'btnDiscardEdit-';
const BTN_EDIT_PREFIX = 'btnHeaderEdit-';
const BTN_ARCH_MAP_PREFIX = 'btnArchitectureMap-';
const WRAP_COPY_LINKS_PREFIX = 'pgCopyLinksWrap-';
const BTN_COPY_LINKS_PREFIX = 'btnCopyLinks-';
const DROPDOWN_COPY_LINKS_PREFIX = 'pgCopyLinksDropdown-';
const BTN_STAR_PREFIX = 'btnHeaderStar-';
const DEFAULT_GERRIT_HOST =
  'https://googleplex-android-review.git.corp.google.com';

export interface ExpandSidebarButtonProps {
  visible?: boolean;
  visibleCommitCount?: number;
}

export function ExpandSidebarButton({
  visible = false,
  visibleCommitCount = 0,
}: ExpandSidebarButtonProps): React.ReactElement {
  const disp = visible ? 'inline-flex' : 'none';
  const lbl =
    visibleCommitCount > 0 ? `Queue (${visibleCommitCount})` : 'Queue';
  return (
    <button
      id="btnExpandSidebar"
      type="button"
      className="btn btn-expand-sidebar"
      htmlStyle={`display:${disp};`}
      htmlOnClick="toggleSidebarCollapsed(false)"
      title="Expand side panel ([)"
      aria-label="Expand side panel"
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <rect x="2" y="2.5" width="12" height="11" rx="2" />
        <line x1="6.5" y1="2.5" x2="6.5" y2="13.5" />
        <polyline points="8.5 6.5 10 8 8.5 9.5" />
      </svg>
      <span id="expandSidebarLabel">{lbl}</span>
    </button>
  );
}

export interface CommitAemuMapButtonProps {
  clNumber: number;
  aemuMapUrl: string;
}

export function CommitAemuMapButton({
  clNumber,
  aemuMapUrl,
}: CommitAemuMapButtonProps): React.ReactElement | null {
  if (!clNumber) {
    return null;
  }
  const mapLabel = '🌐 Architecture Map';
  const iconText = mapLabel.slice(0, 2);
  return (
    <a
      id={`${BTN_ARCH_MAP_PREFIX}${clNumber}`}
      className="btn btn-aemu-map aemu-map-pill"
      href={aemuMapUrl}
      target="_blank"
      rel="noopener noreferrer"
      title={`Visualize ag/${clNumber} blast radius, call flows, and critical paths on go/aemu-map`}
    >
      <span className="aemu-map-icon" aria-hidden="true">
        {iconText}
      </span>
      {' Architecture Map '}
      <span className="aemu-map-ext-arrow" aria-hidden="true">
        ↗
      </span>
    </a>
  );
}

export interface SrSummaryChipProps {
  clNumber: number;
  srSummary: GerritSrSummary;
}

export function SrSummaryChip({
  clNumber,
  srSummary,
}: SrSummaryChipProps): React.ReactElement {
  const icon = srSummary.status === 'submittable' ? '✓' : '✕';
  const textLabel = (srSummary.label || '').replace(/^[✓✕]\s*/, '');
  return (
    <button
      type="button"
      className={`pg-sr-summary-chip ${srSummary.cls}`}
      htmlOnClick={`event.stopPropagation(); openPgSubmitRequirementsAuditDialog(${clNumber})`}
      title={`${srSummary.tip} · Click to audit requirements (Shift+Z)`}
    >
      <span className="pg-sr-summary-icon" aria-hidden="true">
        {icon}
      </span>
      <span>{textLabel}</span>
    </button>
  );
}

interface CopyLinkEntry {
  key: string;
  label: string;
  val: string;
}

function buildCopyLinkEntries(
  clNum: number,
  curPs: number,
  info: GerritChangeReferenceInfo
): CopyLinkEntry[] {
  const rawItems: CopyLinkEntry[] = [
    { key: 'change_id', label: 'Change-Id', val: info.changeId },
    { key: 'commit', label: `Commit (PS ${curPs})`, val: info.commitSha },
    ...(info.repoDownloadCmd
      ? [
          {
            key: 'repo_download',
            label: `Repo Download (PS ${curPs})`,
            val: info.repoDownloadCmd,
          },
        ]
      : []),
    ...(info.gitCheckoutCmd
      ? [
          {
            key: 'git_checkout',
            label: `Git Checkout (PS ${curPs})`,
            val: info.gitCheckoutCmd,
          },
        ]
      : []),
    ...(info.gitCherryPickCmd
      ? [
          {
            key: 'cherry_pick_cmd',
            label: `Git Cherry-Pick (PS ${curPs})`,
            val: info.gitCherryPickCmd,
          },
        ]
      : []),
    ...(info.filePaths && info.filePaths.length > 0
      ? [
          {
            key: 'files',
            label: `Files (${info.filePaths.length})`,
            val: info.filePathsText,
          },
        ]
      : []),
    ...(info.cherryPickOfChange
      ? [
          {
            key: 'cherry_pick_of',
            label: 'Cherry pick of',
            val: info.cherryPickOfRef,
          },
        ]
      : []),
    ...(info.revertOfChange
      ? [
          {
            key: 'revert_of',
            label: 'Revert of',
            val: String(info.revertOfChange),
          },
        ]
      : []),
    { key: 'shortlink', label: 'ag/ shortlink', val: info.shortlink },
    { key: 'number', label: 'Change number', val: String(clNum) },
    { key: 'url', label: 'Gerrit URL', val: info.gerritUrl },
    { key: 'title_url', label: 'Title & Link', val: info.titleAndUrl },
    { key: 'markdown', label: 'Markdown link', val: info.markdown },
  ];
  return rawItems.filter((item) => Boolean(item.val));
}

export interface CopyLinksMenuProps {
  change?: GerritChange | null;
  bundle?: GerritDiffBundle | null;
  info: GerritChangeReferenceInfo;
  isOpen?: boolean;
}

export function CopyLinksMenu({
  change,
  bundle = null,
  info,
  isOpen = false,
}: CopyLinksMenuProps): React.ReactElement | null {
  if (!change || !change.number) {
    return null;
  }
  const clNum = Number(change.number);
  const curPs = Number(bundle?.patch_set || change.patch_set || 1);
  const items = buildCopyLinkEntries(clNum, curPs, info);

  return (
    <div id={`${WRAP_COPY_LINKS_PREFIX}${clNum}`} className="pg-copy-links-wrap">
      <button
        id={`${BTN_COPY_LINKS_PREFIX}${clNum}`}
        type="button"
        className={`pg-icon-btn pg-btn-copy-links ${isOpen ? 'is-active' : ''}`}
        htmlOnClick={`event.stopPropagation(); togglePgCopyLinksMenu(${clNum})`}
        title={`Copy links and references for ag/${clNum} (Shortcut: Shift+L)`}
        aria-label="Copy links and references"
        aria-expanded={isOpen ? 'true' : 'false'}
        aria-haspopup="true"
      >
        <span className="pg-copy-icon" aria-hidden="true">
          ⧉
        </span>
        <span className="pg-copy-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      <div
        id={`${DROPDOWN_COPY_LINKS_PREFIX}${clNum}`}
        className={`pg-copy-links-dropdown ${isOpen ? 'is-open' : ''}`}
        htmlOnClick="event.stopPropagation()"
        role="menu"
        aria-label="Copy references"
      >
        <div className="pg-copy-links-header">
          <span className="pg-copy-links-title">
            📋 Copy Links &amp; References
          </span>
          <button
            type="button"
            className="pg-copy-close-btn"
            htmlOnClick="event.stopPropagation(); closePgCopyLinksMenu()"
            title="Close menu (Esc)"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="pg-copy-links-items">
          {items.map((item) => (
            <div
              key={item.key}
              className="pg-copy-link-item"
              data-key={item.key}
            >
              <div className="pg-copy-link-meta">
                <span className="pg-copy-link-label">{item.label}</span>
                <span className="pg-copy-link-value" title={item.val}>
                  {item.val}
                </span>
              </div>
              <button
                type="button"
                className="pg-btn-copy-pill"
                htmlOnClick={`event.stopPropagation(); copyPgMetaField(${clNum}, '${item.key}', this)`}
                title={`Copy ${item.label}`}
              >
                📋 Copy
              </button>
            </div>
          ))}
        </div>
        <div className="pg-copy-links-footer">
          {info.gitilesUrl ? (
            <a
              className="pg-copy-footer-link"
              href={info.gitilesUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="Open commit in Gitiles ↗"
            >
              Gitiles ↗
            </a>
          ) : null}
          {info.codeSearchUrl ? (
            <a
              className="pg-copy-footer-link"
              href={info.codeSearchUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="Search Change-Id in Code Search ↗"
            >
              Code Search ↗
            </a>
          ) : null}
          <button
            type="button"
            className="pg-copy-footer-btn"
            htmlOnClick={`event.stopPropagation(); closePgCopyLinksMenu(); openPgDownloadDialog(${clNum})`}
            title="Open full Download Patch & Checkout dialog (Shortcut: d)"
          >
            ⬇ Download commands…
          </button>
        </div>
      </div>
    </div>
  );
}

export interface HeaderActionButtonsProps {
  change?: GerritChange | null;
  hasActiveEdit?: boolean;
}

export function HeaderActionButtons({
  change,
  hasActiveEdit = false,
}: HeaderActionButtonsProps): React.ReactElement | null {
  if (!change) {
    return null;
  }
  const num = Number(change.number);
  const isAbandoned = isChangeAbandoned(change);
  const isMerged = isChangeMerged(change);
  const isWip = Boolean(change.work_in_progress);
  const isEditable = !isAbandoned && !isMerged;

  return (
    <div className="pg-header-action-group">
      {hasActiveEdit ? (
        <>
          <span
            className="pg-edit-in-progress-badge"
            title={`Active change edit exists for ag/${num}`}
          >
            ✏️ Edit in progress
          </span>
          <button
            type="button"
            id={`${BTN_PUBLISH_EDIT_PREFIX}${num}`}
            className="btn pg-btn-header-action pg-btn-publish-edit"
            htmlOnClick={`event.stopPropagation(); publishActiveChangeEdit(${num})`}
            title="Publish change edit as new patchset (notify: OWNER)"
          >
            <span className="pg-btn-icon" aria-hidden="true">
              ✓
            </span>{' '}
            Publish Edit
          </button>
          <button
            type="button"
            id={`${BTN_DISCARD_EDIT_PREFIX}${num}`}
            className="btn pg-btn-header-action pg-btn-discard-edit"
            htmlOnClick={`event.stopPropagation(); discardActiveChangeEdit(${num})`}
            title="Discard active change edit"
          >
            <span className="pg-btn-icon" aria-hidden="true">
              ✕
            </span>{' '}
            Discard Edit
          </button>
        </>
      ) : null}
      {isEditable ? (
        isWip ? (
          <button
            type="button"
            id={`${BTN_MARK_READY_PREFIX}${num}`}
            className="btn pg-btn-header-action pg-btn-ready"
            htmlOnClick={`event.stopPropagation(); openPgSetReadyDialog(${num})`}
            title={`Mark change ag/${num} as active / ready for review (Shortcut: Alt+W)`}
          >
            <span className="pg-btn-icon" aria-hidden="true">
              ✓
            </span>{' '}
            Mark as Active
          </button>
        ) : (
          <button
            type="button"
            id={`${BTN_MARK_WIP_PREFIX}${num}`}
            className="btn pg-btn-header-action pg-btn-wip"
            htmlOnClick={`event.stopPropagation(); openPgSetWipDialog(${num})`}
            title={`Mark change ag/${num} as Work in Progress (Shortcut: Alt+W)`}
          >
            <span className="pg-btn-icon" aria-hidden="true">
              🚧
            </span>{' '}
            Mark as WIP
          </button>
        )
      ) : null}
      {isEditable ? (
        <button
          type="button"
          id={`${BTN_EDIT_PREFIX}${num}`}
          className="btn pg-btn-header-action pg-btn-edit-mode"
          htmlOnClick={`event.stopPropagation(); openPgFileEditor(${num})`}
          title="Edit files in browser (Shortcut: e)"
        >
          <span className="pg-btn-icon" aria-hidden="true">
            ✏️
          </span>{' '}
          Edit
        </button>
      ) : null}
      <button
        type="button"
        className="btn pg-btn-header-action pg-btn-followup"
        htmlOnClick={`event.stopPropagation(); openPgFollowUpDialog(${num})`}
        title={`Create follow-up change stacked on ag/${num} (Shortcut: Shift+U)`}
      >
        <span className="pg-btn-icon" aria-hidden="true">
          ↳
        </span>{' '}
        Follow-Up
      </button>
      <button
        type="button"
        className="btn pg-btn-header-action pg-btn-included-in"
        htmlOnClick={`event.stopPropagation(); openPgIncludedInDialog(${num})`}
        title={`Inspect branches and tags containing change ag/${num} (Shortcut: Shift+G)`}
      >
        <span className="pg-btn-icon" aria-hidden="true">
          🏷️
        </span>{' '}
        Included In
      </button>
      <button
        type="button"
        className="btn pg-btn-header-action"
        htmlOnClick={`event.stopPropagation(); openPgDownloadDialog(${num})`}
        title={`Download patch & checkout commands for ag/${num} (Shortcut: d)`}
      >
        <span className="pg-btn-icon" aria-hidden="true">
          ⬇
        </span>{' '}
        Download
      </button>
      {isEditable ? (
        <button
          type="button"
          className="btn pg-btn-header-action"
          htmlOnClick={`event.stopPropagation(); openPgRebaseDialog(${num})`}
          title={`Rebase change ag/${num} on parent branch or base (Shortcut: Alt+U)`}
        >
          <span className="pg-btn-icon" aria-hidden="true">
            ↻
          </span>{' '}
          Rebase
        </button>
      ) : null}
      <button
        type="button"
        className="btn pg-btn-header-action pg-btn-cherrypick"
        htmlOnClick={`event.stopPropagation(); openPgCherryPickDialog(${num})`}
        title={`Cherry pick change ag/${num} to another branch (Shortcut: Shift+C)`}
      >
        <span className="pg-btn-icon" aria-hidden="true">
          🍒
        </span>{' '}
        Cherry Pick
      </button>
      {isMerged ? (
        <button
          type="button"
          id={`${BTN_REVERT_PREFIX}${num}`}
          className="btn pg-btn-header-action pg-btn-revert"
          htmlOnClick={`event.stopPropagation(); openPgRevertDialog(${num})`}
          title={`Revert merged change ag/${num} (creates revert change with notify: OWNER)`}
        >
          <span className="pg-btn-icon" aria-hidden="true">
            ↺
          </span>{' '}
          Revert
        </button>
      ) : isAbandoned ? (
        <button
          type="button"
          className="btn pg-btn-header-action pg-btn-restore"
          htmlOnClick={`event.stopPropagation(); openPgRestoreDialog(${num})`}
          title={`Restore abandoned change ag/${num}`}
        >
          <span className="pg-btn-icon" aria-hidden="true">
            ↺
          </span>{' '}
          Restore
        </button>
      ) : (
        <button
          type="button"
          className="btn pg-btn-header-action pg-btn-abandon"
          htmlOnClick={`event.stopPropagation(); openPgAbandonDialog(${num})`}
          title={`Abandon change ag/${num}`}
        >
          <span className="pg-btn-icon" aria-hidden="true">
            ⊘
          </span>{' '}
          Abandon
        </button>
      )}
    </div>
  );
}

export interface ChangeHeaderTitleBarProps {
  change: GerritChange;
  bundle?: GerritDiffBundle | null;
  statusInfo: GerritHeaderStatusInfo;
  srSummary: GerritSrSummary;
  refInfo: GerritChangeReferenceInfo;
  isCopyMenuOpen?: boolean;
  isStarred?: boolean;
  isViewerTurn?: boolean;
  expandSidebarVisible?: boolean;
  visibleCommitCount?: number;
}

export function ChangeHeaderTitleBar({
  change,
  bundle = null,
  statusInfo,
  srSummary,
  refInfo,
  isCopyMenuOpen = false,
  isStarred = false,
  isViewerTurn = false,
  expandSidebarVisible = false,
  visibleCommitCount = 0,
}: ChangeHeaderTitleBarProps): React.ReactElement {
  const num = Number(change.number);
  const gerritUrl = change.url || `${DEFAULT_GERRIT_HOST}/c/${num}`;
  const isPureRebase = Boolean(bundle?.is_pure_rebase);
  const curPs = Number(change.patch_set || 1);
  const isHeaderUrgent = Boolean(
    isViewerTurn &&
      ((change.attention_waiting_display &&
        /[dw]/.test(change.attention_waiting_display)) ||
        Number(change.unresolved_comments || 0) > 0)
  );
  const headerWaitNote = change.attention_waiting_display
    ? ` (waiting ${change.attention_waiting_display})`
    : '';
  const turnTitle = `Your turn in Gerrit Attention Set${
    isHeaderUrgent ? ` (Urgent${headerWaitNote})` : headerWaitNote
  }`;

  return (
    <div className="pg-header-title">
      <ExpandSidebarButton
        visible={expandSidebarVisible}
        visibleCommitCount={visibleCommitCount}
      />
      <span
        className={`pg-status-badge ${statusInfo.cls}`}
        title={statusInfo.tip}
      >
        {statusInfo.label}
      </span>
      {statusInfo.secondaryCls && statusInfo.secondaryLabel ? (
        <span
          className={`pg-status-badge ${statusInfo.secondaryCls}`}
          title={statusInfo.secondaryTip || ''}
        >
          {statusInfo.secondaryLabel}
        </span>
      ) : null}
      {isViewerTurn ? (
        <span
          className={`pg-status-badge status-attention is-pulsing ${
            isHeaderUrgent ? 'is-urgent' : ''
          }`}
          title={turnTitle}
        >
          <span className="pg-turn-dot" aria-hidden="true" /> Your Turn
        </span>
      ) : null}
      {isPureRebase ? (
        <span
          className="pg-status-badge status-rebase"
          title={`PS${curPs - 1}→PS${curPs} is a pure rebase (0 code changes)`}
        >
          Pure Rebase
        </span>
      ) : null}
      <SrSummaryChip clNumber={num} srSummary={srSummary} />
      <button
        id={`${BTN_STAR_PREFIX}${num}`}
        type="button"
        className={`pg-header-star-btn ${isStarred ? 'is-starred' : ''}`}
        htmlOnClick={`togglePgStarChange(${num}, event)`}
        title={
          isStarred
            ? `Unstar change ag/${num} (*)`
            : `Star change ag/${num} (*)`
        }
        aria-label={isStarred ? 'Starred' : 'Not starred'}
      >
        <span className="pg-star-icon" aria-hidden="true">
          {isStarred ? '★' : '☆'}
        </span>
        <span className="pg-star-label">{isStarred ? 'Starred' : 'Star'}</span>
      </button>
      <a
        className="pg-change-number commit-cl-id"
        href={gerritUrl}
        target="_blank"
        rel="noopener noreferrer"
        title={`Open ag/${num} in Gerrit ↗`}
      >
        {num}
      </a>
      <span className="pg-header-colon">:</span>
      <h1 className="pg-header-subject" title={change.subject || ''}>
        {change.subject || ''}
      </h1>
      <CopyLinksMenu
        change={change}
        bundle={bundle}
        info={refInfo}
        isOpen={isCopyMenuOpen}
      />
    </div>
  );
}

export function renderExpandSidebarButtonHtml(
  visible = false,
  visibleCommitCount = 0
): string {
  return renderReactToHtml(
    <ExpandSidebarButton
      visible={visible}
      visibleCommitCount={visibleCommitCount}
    />
  );
}

export function renderCommitAemuMapButtonHtml(
  clNumber: number,
  aemuMapUrl: string
): string {
  return renderReactToHtml(
    <CommitAemuMapButton clNumber={clNumber} aemuMapUrl={aemuMapUrl} />
  );
}

export function renderPgSrSummaryChipHtml(
  clNumber: number,
  srSummary: GerritSrSummary
): string {
  return renderReactToHtml(
    <SrSummaryChip clNumber={clNumber} srSummary={srSummary} />
  );
}

export function renderPgCopyLinksMenuHtml(
  change: GerritChange | null | undefined,
  bundle: GerritDiffBundle | null | undefined,
  info: GerritChangeReferenceInfo,
  isOpen = false
): string {
  return renderReactToHtml(
    <CopyLinksMenu
      change={change}
      bundle={bundle}
      info={info}
      isOpen={isOpen}
    />
  );
}

export function renderPgHeaderActionButtonsHtml(
  change: GerritChange | null | undefined,
  hasActiveEdit = false
): string {
  return renderReactToHtml(
    <HeaderActionButtons change={change} hasActiveEdit={hasActiveEdit} />
  );
}

export function renderPgChangeHeaderTitleBarHtml(
  props: ChangeHeaderTitleBarProps
): string {
  return renderReactToHtml(<ChangeHeaderTitleBar {...props} />);
}
