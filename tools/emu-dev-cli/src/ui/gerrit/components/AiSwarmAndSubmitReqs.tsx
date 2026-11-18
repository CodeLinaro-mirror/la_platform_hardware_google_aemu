import React from 'react';
import { renderReactToHtml } from '../runtime';
import { GerritChange } from '../types';

export interface AiAlternativeItem {
  id?: string;
  verdict_tone?: string;
  verdict_icon?: string;
  verdict_label?: string;
  title?: string;
  how_it_works?: string;
  tradeoff?: string;
}

export interface AiAnalysisBannerProps {
  num: number;
  patchSet?: number;
  status: string;
  errorMsg?: string;
}

export function AiAnalysisStatusBanner(props: AiAnalysisBannerProps): React.ReactElement {
  const { num, patchSet = 1, status, errorMsg } = props;
  if (status === 'thinking') {
    return (
      <div id={`aiAnalysisPanel-${num}`} className="ai-intangibles-panel" data-ai-cl={String(num)}>
        <div className="ai-intangibles-head">
          <div className="ai-intangibles-head-left">
            <span className="btn-spinner"></span>
            <span className="ai-intangibles-title">
              {`Problem & Alternatives · Analyzing ag/${num} (PS${patchSet})…`}
            </span>
            <span style={{ fontSize: '11.5px', color: 'var(--g-muted)' }}>
              Evaluating underlying problem, chosen approach, tradeoffs &amp; codebase alternatives
            </span>
          </div>
          <div className="ai-intangibles-actions">
            <button
              type="button"
              className="ai-intangibles-btn"
              onClick={`cancelClAiAnalysis(${num})` as unknown as React.MouseEventHandler}
            >
              ✕ Cancel
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div id={`aiAnalysisPanel-${num}`} className="ai-intangibles-panel" data-ai-cl={String(num)}>
      <div className="ai-intangibles-head">
        <div className="ai-intangibles-head-left">
          <span className="ai-intangibles-title" style={{ color: '#fca5a5' }}>
            {`⚠ Problem & Alternatives Unavailable (ag/${num})`}
          </span>
          <span style={{ fontSize: '11.5px', color: 'var(--g-muted)' }}>
            {errorMsg || 'Unknown error'}
          </span>
        </div>
        <div className="ai-intangibles-actions">
          <button
            type="button"
            className="ai-intangibles-btn"
            onClick={`analyzeClIntangibles(${num}, true)` as unknown as React.MouseEventHandler}
          >
            ↻ Retry
          </button>
          <button
            type="button"
            className="ai-intangibles-btn"
            onClick={`hideAiAnalysisForCl(${num})` as unknown as React.MouseEventHandler}
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}

export interface AiAlternativeCardProps {
  num: number;
  alt: AiAlternativeItem;
  howItWorksHtml: string;
  tradeoffHtml?: string;
}

export function AiAlternativeCard(props: AiAlternativeCardProps): React.ReactElement {
  const { num, alt, howItWorksHtml, tradeoffHtml } = props;
  const rawAltId = String(alt.id || 'alt-1');
  const vTone = String(alt.verdict_tone || 'focus');
  const verdictIcon = String(alt.verdict_icon || '⚖️');
  const verdictLabel = String(alt.verdict_label || 'Alternative');
  return (
    <div
      id={`aiAltCard-${num}-${rawAltId}`}
      className={`ai-alt-card tone-${vTone}`}
      data-cl={String(num)}
      data-alt-id={rawAltId}
    >
      <div className="ai-alt-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }}>
          <span className="ai-alt-verdict">{`${verdictIcon} ${verdictLabel}`}</span>
          <strong style={{ color: '#f1f5f9' }}>{alt.title || ''}</strong>
        </div>
        <div className="ai-intangibles-actions">
          <button
            type="button"
            className="ai-intangibles-btn"
            onClick={`openAiAlternativeInComposer(${num}, '${rawAltId}', 'ask')` as unknown as React.MouseEventHandler}
            title="Ask Agent to check how feasible this alternative is in the repo"
          >
            💬 Explore with Agent
          </button>
          <button
            type="button"
            className="ai-intangibles-btn"
            onClick={`openAiAlternativeInComposer(${num}, '${rawAltId}', 'comment')` as unknown as React.MouseEventHandler}
            title="Draft a Gerrit question to the author about this alternative"
          >
            ✍️ Ask Author
          </button>
        </div>
      </div>
      <div
        style={{ color: '#e2e8f0' }}
        dangerouslySetInnerHTML={{ __html: howItWorksHtml }}
      />
      {tradeoffHtml ? (
        <div
          className="ai-alt-tradeoff"
          dangerouslySetInnerHTML={{ __html: `<strong>Tradeoff:</strong> ${tradeoffHtml}` }}
        />
      ) : null}
    </div>
  );
}

export interface CommitAiAnalysisPanelProps {
  num: number;
  isCollapsed: boolean;
  scopeTone?: string;
  scopeIcon?: string;
  scopeLabel?: string;
  analyzedByLdap?: string;
  shared?: boolean;
  bottomLineHtml?: string;
  underlyingProblemHtml?: string;
  chosenSolutionHtml?: string;
  tradeoffsHtmlList?: string[];
  alternativesCount?: number;
  alternativesCardsHtml?: string;
}

export function CommitAiAnalysisPanel(props: CommitAiAnalysisPanelProps): React.ReactElement {
  const {
    num,
    isCollapsed,
    scopeTone = 'focus',
    scopeIcon = '🧭',
    scopeLabel = 'Design Assessment',
    analyzedByLdap = '',
    shared = false,
    bottomLineHtml = '',
    underlyingProblemHtml = '',
    chosenSolutionHtml = '',
    tradeoffsHtmlList = [],
    alternativesCount = 0,
    alternativesCardsHtml = '',
  } = props;

  return (
    <div
      id={`aiAnalysisPanel-${num}`}
      className={`ai-intangibles-panel${isCollapsed ? ' collapsed' : ''}`}
      data-ai-cl={String(num)}
    >
      <div className="ai-intangibles-head">
        <div className="ai-intangibles-head-left">
          <span className="ai-intangibles-title">{`🧠 Problem & Alternatives · ag/${num}`}</span>
          <span className={`ai-scope-badge tone-${scopeTone}`}>
            {`${scopeIcon} ${scopeLabel}`}
          </span>
          {analyzedByLdap || shared ? (
            <span
              className="qa-group-badge"
              title={`Shared team AI analysis${analyzedByLdap ? ` · generated by @${analyzedByLdap}` : ''}`}
            >
              {`👥 Shared${analyzedByLdap ? ` · @${analyzedByLdap}` : ''}`}
            </span>
          ) : null}
        </div>
        <div className="ai-intangibles-actions">
          <button
            type="button"
            className="ai-intangibles-btn"
            onClick={`analyzeClIntangibles(${num}, true)` as unknown as React.MouseEventHandler}
            title={`Re-run Problem & Alternatives analysis on ag/${num}`}
          >
            ↻ Re-run
          </button>
          <button
            type="button"
            className="ai-intangibles-btn ai-collapse-btn"
            onClick={`toggleAiAnalysisCollapse(${num})` as unknown as React.MouseEventHandler}
          >
            {isCollapsed ? 'Expand ▾' : 'Collapse ▴'}
          </button>
          <button
            type="button"
            className="ai-intangibles-btn"
            onClick={`hideAiAnalysisForCl(${num})` as unknown as React.MouseEventHandler}
            title="Hide Problem & Alternatives panel"
          >
            ✕
          </button>
        </div>
      </div>
      <div className="ai-intangibles-body">
        {bottomLineHtml ? (
          <div className="ai-bottom-line" dangerouslySetInnerHTML={{ __html: bottomLineHtml }} />
        ) : null}
        <div className="ai-intangibles-grid">
          <div className="ai-intangibles-box">
            <div className="ai-intangibles-box-label">Underlying Problem</div>
            <div dangerouslySetInnerHTML={{ __html: underlyingProblemHtml }} />
          </div>
          <div className="ai-intangibles-box">
            <div className="ai-intangibles-box-label">Chosen Solution &amp; Tradeoffs</div>
            <div dangerouslySetInnerHTML={{ __html: chosenSolutionHtml }} />
            {tradeoffsHtmlList.length > 0 ? (
              <ul className="ai-tradeoff-list">
                {tradeoffsHtmlList.map((itemHtml, idx) => (
                  <li key={idx} dangerouslySetInnerHTML={{ __html: itemHtml }} />
                ))}
              </ul>
            ) : null}
          </div>
        </div>
        {alternativesCount > 0 ? (
          <div>
            <div className="ai-alt-section-title">{`Alternative Solutions (${alternativesCount})`}</div>
            <div
              className="ai-alt-list"
              dangerouslySetInnerHTML={{ __html: alternativesCardsHtml }}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export interface SwarmAgentStatus {
  role?: string;
  specialist?: string;
  status?: string;
  finding_count?: number;
  duration_sec?: number;
}

export interface SwarmFindingItem {
  file?: string;
  line?: number;
  category?: string;
  severity?: string;
  message?: string;
  suggested_fix?: string;
  locHtml?: string;
}

export interface SwarmStateEntry {
  state?: string;
  phase?: string;
  completed_specialists?: number;
  total_specialists?: number;
  total_findings?: number;
  wall_clock_sec?: number;
  parallel_speedup?: number;
  agents?: SwarmAgentStatus[];
  findings?: SwarmFindingItem[];
}

export function ClSwarmBadge(props: { sw?: SwarmStateEntry | null; elapsedSec?: number }): React.ReactElement | null {
  const { sw, elapsedSec = 0 } = props;
  if (!sw) return null;
  if (sw.state === 'running') {
    const done = Number(sw.completed_specialists || 0);
    const total = Number(sw.total_specialists || 8);
    return (
      <span className="slot slot-swarm-running">
        <span className="swarm-spinner"></span>
        {` Review Swarm · ${done}/${total} (${elapsedSec}s)`}
      </span>
    );
  }
  if (sw.state === 'completed') {
    const findings = Number(sw.total_findings || (sw.findings ? sw.findings.length : 0));
    const wall = sw.wall_clock_sec ? `${Number(sw.wall_clock_sec).toFixed(1)}s` : 'done';
    if (findings === 0) {
      return <span className="slot slot-swarm-clean">{`✓ Swarm Clean (${wall})`}</span>;
    }
    return (
      <span className="slot slot-swarm-findings">
        {`★ Swarm: ${findings} finding${findings === 1 ? '' : 's'} (${wall})`}
      </span>
    );
  }
  return null;
}

export function SpecialistChips(props: {
  agents?: SwarmAgentStatus[];
  isRunning?: boolean;
  defaultSpecialists?: SwarmAgentStatus[];
}): React.ReactElement {
  const { agents, isRunning = false, defaultSpecialists = [] } = props;
  const list = Array.isArray(agents) && agents.length > 0 ? agents : defaultSpecialists;
  return (
    <>
      {list.map((a, idx) => {
        const roleLabel = String(a.role || a.specialist || 'Specialist').replace(/\s+Auditor$/i, '');
        const st = a.status || (isRunning ? 'running' : 'done');
        if (st === 'done') {
          const fCnt = Number(a.finding_count || 0);
          const dur = a.duration_sec != null ? ` · ${Number(a.duration_sec).toFixed(1)}s` : '';
          if (fCnt > 0) {
            return (
              <span key={idx} className="swarm-spec-chip done-finding">
                {`★ ${roleLabel} (${fCnt}${dur})`}
              </span>
            );
          }
          return (
            <span key={idx} className="swarm-spec-chip done-clean">
              {`✓ ${roleLabel}${dur}`}
            </span>
          );
        }
        return (
          <span key={idx} className="swarm-spec-chip running">
            <span className="swarm-spinner" style={{ width: '9px', height: '9px', borderWidth: '1.5px' }}></span>
            {` ${roleLabel}`}
          </span>
        );
      })}
    </>
  );
}

export function SwarmFindingsList(props: {
  clNum: number;
  findings?: SwarmFindingItem[];
}): React.ReactElement | null {
  const { clNum, findings } = props;
  if (!Array.isArray(findings) || findings.length === 0) return null;
  return (
    <>
      {findings.map((f, idx) => {
        const loc = `${f.file || '/COMMIT_MSG'}:${f.line || 1}`;
        const cat = f.category || 'review';
        const sev = f.severity || 'blocking';
        return (
          <div key={idx} className="swarm-finding" style={{ margin: '6px 12px 10px 12px' }}>
            <div className="swarm-finding-head">
              <span>
                {'★ Swarm Finding · '}
                {f.locHtml ? (
                  <code dangerouslySetInnerHTML={{ __html: f.locHtml }} />
                ) : (
                  <code>{loc}</code>
                )}
                {` [${cat} · ${sev}]`}
              </span>
              <button
                className="nudge-btn"
                onClick={`postSwarmFindingsToGerrit(${clNum}, this)` as unknown as React.MouseEventHandler}
              >
                Post to Gerrit (notify: OWNER)
              </button>
            </div>
            <div style={{ color: 'var(--g-text)', fontSize: '12px' }}>{f.message || ''}</div>
            {f.suggested_fix ? (
              <div
                style={{
                  marginTop: '6px',
                  padding: '5px 8px',
                  background: '#18191d',
                  border: '1px solid var(--g-border)',
                  borderRadius: '4px',
                  fontFamily: 'var(--g-mono)',
                  fontSize: '11px',
                  color: '#b7e3c4',
                }}
              >
                {`Suggested fix: ${f.suggested_fix}`}
              </div>
            ) : null}
          </div>
        );
      })}
    </>
  );
}

export function CommitSwarmPanel(props: {
  clNum: number;
  sw?: SwarmStateEntry | null;
  elapsedSec?: number;
  defaultSpecialists?: SwarmAgentStatus[];
}): React.ReactElement | null {
  const { clNum, sw, elapsedSec = 0, defaultSpecialists = [] } = props;
  if (!sw) return null;
  if (sw.state === 'running') {
    const done = Number(sw.completed_specialists || 0);
    const total = Math.max(1, Number(sw.total_specialists || 8));
    const pct = Math.min(96, Math.max(10, Math.round((done / total) * 88) + 8));
    const phaseLabel =
      sw.phase === 'auditing'
        ? `Auditing Diff in Parallel · ${done}/${total} Specialists Done · ${elapsedSec}s`
        : `Preparing Bundle & Launching 8 Specialists · ${done}/${total} · ${elapsedSec}s`;
    return (
      <div className="swarm-live-panel">
        <div className="swarm-progress-track">
          <div className="swarm-progress-bar" style={{ width: `${pct}%` }}></div>
        </div>
        <div className="swarm-panel-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="swarm-spinner"></span>
            <strong style={{ color: '#d2c4f0' }}>
              {`8-Specialist Review Swarm Running on ag/${clNum}`}
            </strong>
          </div>
          <span style={{ fontSize: '11.5px', fontWeight: 600, color: 'var(--g-muted)' }}>
            {phaseLabel}
          </span>
        </div>
        <div className="swarm-specialist-grid">
          <SpecialistChips
            agents={sw.agents}
            isRunning={true}
            defaultSpecialists={defaultSpecialists}
          />
        </div>
      </div>
    );
  }
  if (sw.state === 'completed') {
    const findings = Array.isArray(sw.findings) ? sw.findings : [];
    const fCnt = Number(sw.total_findings != null ? sw.total_findings : findings.length);
    const wall = sw.wall_clock_sec ? `${Number(sw.wall_clock_sec).toFixed(1)}s` : '';
    const speedup = sw.parallel_speedup ? ` · ${Number(sw.parallel_speedup).toFixed(2)}x parallel speedup` : '';
    const cleanCls = fCnt === 0 ? 'completed-clean' : '';
    const headline =
      fCnt === 0
        ? '✓ 8-Specialist Review Swarm Clean (0 findings)'
        : `★ 8-Specialist Review Swarm Complete · ${fCnt} Verified Finding${fCnt === 1 ? '' : 's'}`;
    const headColor = fCnt === 0 ? '#96d9ab' : '#d2c4f0';
    return (
      <div className={`swarm-live-panel ${cleanCls}`}>
        <div className="swarm-panel-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <strong style={{ color: headColor }}>{headline}</strong>
            <span style={{ fontSize: '11.5px', color: 'var(--g-muted)' }}>{wall + speedup}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            {fCnt > 0 ? (
              <button
                className="nudge-btn"
                onClick={`postSwarmFindingsToGerrit(${clNum}, this)` as unknown as React.MouseEventHandler}
              >
                {`Post Finding${fCnt === 1 ? '' : 's'} to Gerrit (notify: OWNER)`}
              </button>
            ) : null}
            <button
              className="nudge-btn"
              onClick={`triggerHarnessForCl(${clNum}, 'review-swarm', this)` as unknown as React.MouseEventHandler}
            >
              ↻ Re-Run Swarm
            </button>
          </div>
        </div>
        <div className="swarm-specialist-grid">
          <SpecialistChips
            agents={sw.agents}
            isRunning={false}
            defaultSpecialists={defaultSpecialists}
          />
        </div>
        <SwarmFindingsList clNum={clNum} findings={findings} />
      </div>
    );
  }
  return null;
}

export interface SubmitRequirementItem {
  name?: string;
  status?: string;
  description?: string;
  expression?: string;
  applicability_expression?: string;
  override_expression?: string;
  passing_atoms?: string[];
  failing_atoms?: string[];
}

export function VoteBoxChip(props: {
  name: string;
  val: number;
  labelName: string;
  avatarBg: string;
  initial: string;
}): React.ReactElement {
  const { name, val, labelName, avatarBg, initial } = props;
  const v = Number(val || 0);
  const cls = v >= 2 ? 'v-pos2' : v === 1 ? 'v-pos1' : v === -1 ? 'v-neg1' : 'v-neg2';
  const fmt = v > 0 ? `+${v}` : String(v);
  return (
    <span className={`pg-vote-box ${cls}`} title={`${name}: ${labelName} ${fmt}`}>
      <span className="pg-account-avatar" style={{ background: avatarBg }}>
        {initial}
      </span>
      <span>{fmt}</span>
    </span>
  );
}

export function SrAtomsList(props: { sr: SubmitRequirementItem }): React.ReactElement | null {
  const { sr } = props;
  const passAtoms = Array.isArray(sr.passing_atoms) ? sr.passing_atoms : [];
  const failAtoms = Array.isArray(sr.failing_atoms) ? sr.failing_atoms : [];
  if (passAtoms.length === 0 && failAtoms.length === 0) return null;
  return (
    <ul className="pg-sr-atom-list">
      {passAtoms.map((a, idx) => (
        <li key={`pass-${idx}`} className="pg-sr-atom pg-sr-atom-pass">
          <span className="atom-icon" aria-hidden="true">
            ✓
          </span>{' '}
          {a}
        </li>
      ))}
      {failAtoms.map((a, idx) => (
        <li key={`fail-${idx}`} className="pg-sr-atom pg-sr-atom-fail">
          <span className="atom-icon" aria-hidden="true">
            ✕
          </span>{' '}
          {a}
        </li>
      ))}
    </ul>
  );
}

export function SrActionBtn(props: { clNum: number; sr: SubmitRequirementItem }): React.ReactElement {
  const { clNum, sr } = props;
  const name = sr.name || '';
  if (name === 'Code-Review' || name === 'Verified') {
    return (
      <button
        type="button"
        className="pg-btn-subtle"
        onClick={`event.stopPropagation(); openReplyDialog({ focusComment: false })` as unknown as React.MouseEventHandler}
        title={`Open Reply dialog to vote ${name}`}
      >
        {`Vote ${name} (r)`}
      </button>
    );
  }
  if (name === 'Presubmit-Verified') {
    return (
      <button
        type="button"
        className="pg-btn-subtle"
        onClick={`event.stopPropagation(); selectPgChangeTab(${clNum}, 'checks')` as unknown as React.MouseEventHandler}
        title="Inspect TreeHugger presubmit checks in Checks tab"
      >
        Inspect Checks Tab
      </button>
    );
  }
  return (
    <button
      type="button"
      className="pg-btn-subtle"
      onClick={`event.stopPropagation(); openReplyDialog({ focusComment: false })` as unknown as React.MouseEventHandler}
      title="Open Reply dialog"
    >
      Reply / Vote
    </button>
  );
}

export function SrDetailRow(props: { clNum: number; sr: SubmitRequirementItem }): React.ReactElement {
  const { clNum, sr } = props;
  const status = String(sr.status || 'UNSATISFIED').toUpperCase();
  let statusCls = 'tag-unsatisfied';
  if (status === 'SATISFIED' || status === 'OVERRIDDEN') statusCls = 'tag-satisfied';
  else if (status === 'BLOCKED') statusCls = 'tag-blocked';
  else if (status === 'NOT_APPLICABLE') statusCls = 'tag-na';

  return (
    <tr className="pg-sr-detail-row">
      <td colSpan={4} className="pg-sr-detail-cell">
        <div className="pg-sr-detail-box">
          <div className="pg-sr-detail-header">
            <span className={`pg-sr-status-tag ${statusCls}`}>{status}</span>
            {sr.description ? <span className="pg-sr-desc">{sr.description}</span> : null}
          </div>
          {sr.expression ? (
            <div className="pg-sr-expression-block">
              <span className="pg-sr-expr-label">Expression:</span>
              <code className="pg-sr-code">{sr.expression}</code>
            </div>
          ) : null}
          <SrAtomsList sr={sr} />
          <div className="pg-sr-detail-actions">
            <SrActionBtn clNum={clNum} sr={sr} />
          </div>
        </div>
      </td>
    </tr>
  );
}

export interface CrSubmitRequirementRowProps {
  satisfied: boolean;
  minVote: number;
  reqCr2: number;
  doneCr2: number;
  chipsHtml: string;
}

export function CrSubmitRequirementRow(props: CrSubmitRequirementRowProps): React.ReactElement {
  const { satisfied, minVote, reqCr2, doneCr2, chipsHtml } = props;
  const rowTitle =
    reqCr2 >= 2
      ? `Requires ${reqCr2} human Code-Review +2 approvals (click to Reply)`
      : 'Requires Code-Review +2 approval (click to Reply)';

  let hintNode: React.ReactNode = null;
  if (minVote > -2 && doneCr2 < reqCr2 && reqCr2 >= 2) {
    hintNode = (
      <span className="pg-sr-hint">
        {doneCr2 === 0 ? `Requires ${reqCr2} votes` : `${reqCr2 - doneCr2} more +2 needed`}
      </span>
    );
  } else if (!chipsHtml) {
    hintNode = <span className="pg-no-votes">No votes</span>;
  }

  return (
    <tr
      className="pg-sr-row"
      onClick={`event.stopPropagation(); openReplyDialog({ focusComment: false })` as unknown as React.MouseEventHandler}
      title={rowTitle}
    >
      <td className="pg-sr-icon-cell">
        {satisfied ? (
          <span className="pg-sr-icon sr-ok" title="Submit requirement satisfied">
            ✓
          </span>
        ) : (
          <span
            className="pg-sr-icon sr-unsat"
            title={
              minVote <= -2
                ? 'Blocked by Code-Review -2'
                : `Needs ${reqCr2} human Code-Review +2 approval${reqCr2 === 1 ? '' : 's'}`
            }
          >
            ✕
          </span>
        )}
      </td>
      <td className="pg-sr-name">Code-Review</td>
      <td className="pg-sr-votes">
        {chipsHtml ? <span dangerouslySetInnerHTML={{ __html: chipsHtml }} /> : null}
        {hintNode}
      </td>
    </tr>
  );
}

export interface PvSubmitRequirementRowProps {
  clNum: number;
  satisfied: boolean;
  running: boolean;
  tooltip: string;
  votesHtml: string;
}

export function PvSubmitRequirementRow(props: PvSubmitRequirementRowProps): React.ReactElement {
  const { clNum, satisfied, running, tooltip, votesHtml } = props;
  return (
    <tr
      className="pg-sr-row"
      onClick={`event.stopPropagation(); selectPgChangeTab(${clNum}, 'checks')` as unknown as React.MouseEventHandler}
      title={tooltip || 'Click to inspect TreeHugger presubmit runs in Checks tab'}
    >
      <td className="pg-sr-icon-cell">
        {satisfied ? (
          <span className="pg-sr-icon sr-ok" title="Submit requirement satisfied">
            ✓
          </span>
        ) : running ? (
          <span className="pg-sr-icon sr-warn" title={tooltip || 'Presubmit running'}>
            ◷
          </span>
        ) : (
          <span className="pg-sr-icon sr-unsat" title={tooltip || 'Needs Presubmit-Verified +2'}>
            ✕
          </span>
        )}
      </td>
      <td className="pg-sr-name">Presubmit-Verified</td>
      <td className="pg-sr-votes" dangerouslySetInnerHTML={{ __html: votesHtml }} />
    </tr>
  );
}

export interface TriggerVotesSectionProps {
  prVotesHtml: string;
  asVotesHtml: string;
  extraPills: Array<{ label: string; chipsHtml: string }>;
}

export function TriggerVotesSection(props: TriggerVotesSectionProps): React.ReactElement {
  const { prVotesHtml, asVotesHtml, extraPills } = props;
  return (
    <div className="pg-trigger-votes-section">
      <h4 className="pg-metadata-title">Trigger Votes</h4>
      <div className="pg-trigger-list">
        <button
          type="button"
          className="pg-trigger-pill"
          onClick={`event.stopPropagation(); openReplyDialog({ focusComment: false })` as unknown as React.MouseEventHandler}
          title="Presubmit-Ready trigger vote (click to stage via Reply dialog)"
        >
          <span>Presubmit-Ready</span>
          <span dangerouslySetInnerHTML={{ __html: prVotesHtml }} />
        </button>
        <button
          type="button"
          className="pg-trigger-pill"
          onClick={`event.stopPropagation(); openReplyDialog({ focusComment: false })` as unknown as React.MouseEventHandler}
          title="Autosubmit trigger vote (click to stage via Reply dialog)"
        >
          <span>Autosubmit</span>
          <span dangerouslySetInnerHTML={{ __html: asVotesHtml }} />
        </button>
        {extraPills.map((ep, idx) => (
          <button
            key={idx}
            type="button"
            className="pg-trigger-pill"
            onClick={`event.stopPropagation(); openReplyDialog({ focusComment: false })` as unknown as React.MouseEventHandler}
            title="Click to open Reply dialog"
          >
            <span>{ep.label}</span>
            <span dangerouslySetInnerHTML={{ __html: ep.chipsHtml }} />
          </button>
        ))}
      </div>
    </div>
  );
}

export interface SubmitRequirementsSectionProps {
  clNum: number;
  satisfiedCount: number;
  totalCount: number;
  rowsHtml: string;
  triggerVotesHtml: string;
}

export function SubmitRequirementsSection(props: SubmitRequirementsSectionProps): React.ReactElement {
  const { clNum, satisfiedCount, totalCount, rowsHtml, triggerVotesHtml } = props;
  const allSat = totalCount > 0 && satisfiedCount === totalCount;
  const badgeCls = allSat ? 'sr-badge-ok' : 'sr-badge-pending';
  return (
    <section className="pg-submit-requirements">
      <div className="pg-sr-header">
        <h3 className="pg-metadata-title">
          {'Submit Requirements '}
          <span className={`pg-sr-count-badge ${badgeCls}`}>{`${satisfiedCount}/${totalCount}`}</span>
        </h3>
        <button
          type="button"
          className="pg-btn-subtle pg-btn-sr-audit"
          onClick={`event.stopPropagation(); openPgSubmitRequirementsAuditDialog(${clNum})` as unknown as React.MouseEventHandler}
          title="Audit all submit requirements and trigger votes in detail (Shortcut: Shift+Z)"
        >
          <span>📋 Audit</span>
        </button>
      </div>
      <table className="pg-sr-table">
        <tbody dangerouslySetInnerHTML={{ __html: rowsHtml }} />
      </table>
      <div dangerouslySetInnerHTML={{ __html: triggerVotesHtml }} />
    </section>
  );
}

export interface SrAuditCardProps {
  clNum: number;
  sr: SubmitRequirementItem;
  highlightedExprHtml?: string;
  highlightedAppHtml?: string;
  highlightedOvrHtml?: string;
  crChipsHtml?: string;
}

export function SrAuditCard(props: SrAuditCardProps): React.ReactElement {
  const {
    clNum,
    sr,
    highlightedExprHtml = '',
    highlightedAppHtml = '',
    highlightedOvrHtml = '',
    crChipsHtml = '',
  } = props;
  const name = sr.name || 'Requirement';
  const status = String(sr.status || 'UNSATISFIED').toUpperCase();
  let cardCls = 'is-unsatisfied';
  let icon = '✕';
  let iconCls = 'icon-unsatisfied';
  const statusLabel = status;

  if (status === 'SATISFIED') {
    cardCls = 'is-satisfied';
    icon = '✓';
    iconCls = 'icon-satisfied';
  } else if (status === 'OVERRIDDEN') {
    cardCls = 'is-overridden';
    icon = '↷';
    iconCls = 'icon-overridden';
  } else if (status === 'BLOCKED') {
    cardCls = 'is-blocked';
    icon = '✕';
    iconCls = 'icon-blocked';
  } else if (status === 'NOT_APPLICABLE') {
    cardCls = 'is-na';
    icon = '—';
    iconCls = 'icon-na';
  } else if (status === 'RUNNING') {
    cardCls = 'is-running';
    icon = '◷';
    iconCls = 'icon-unsatisfied';
  }

  const passAtoms = Array.isArray(sr.passing_atoms) ? sr.passing_atoms : [];
  const failAtoms = Array.isArray(sr.failing_atoms) ? sr.failing_atoms : [];

  return (
    <div
      className={`pg-sr-audit-card ${cardCls}`}
      data-sr-name={name}
      data-sr-status={status}
    >
      <div className="pg-sr-card-head">
        <div className="pg-sr-card-head-left">
          <span className={`pg-sr-card-icon ${iconCls}`} aria-hidden="true">
            {icon}
          </span>
          <span className="pg-sr-card-name">{name}</span>
          <span className={`pg-sr-status-tag tag-${status.toLowerCase()}`}>{statusLabel}</span>
        </div>
        <span className="pg-meta-pill" style={{ fontSize: '11px' }}>
          {status === 'NOT_APPLICABLE' ? 'Optional / N/A' : 'Blocking'}
        </span>
      </div>
      {sr.description ? <div className="pg-sr-card-desc">{sr.description}</div> : null}
      {sr.applicability_expression ? (
        <div className="pg-sr-card-expr-row">
          <span className="pg-sr-card-expr-label">Applicable if:</span>
          <code
            className="pg-sr-card-code"
            dangerouslySetInnerHTML={{ __html: highlightedAppHtml }}
          />
        </div>
      ) : null}
      {sr.expression ? (
        <div className="pg-sr-card-expr-row">
          <span className="pg-sr-card-expr-label">Submittable if:</span>
          <code
            className="pg-sr-card-code"
            dangerouslySetInnerHTML={{ __html: highlightedExprHtml }}
          />
        </div>
      ) : null}
      {sr.override_expression ? (
        <div className="pg-sr-card-expr-row">
          <span className="pg-sr-card-expr-label">Overridden if:</span>
          <code
            className="pg-sr-card-code"
            dangerouslySetInnerHTML={{ __html: highlightedOvrHtml }}
          />
        </div>
      ) : null}
      {passAtoms.length > 0 || failAtoms.length > 0 ? (
        <ul className="pg-sr-atoms-list">
          {passAtoms.map((a, idx) => (
            <li key={`p-${idx}`} className="pg-sr-atom-item is-pass">
              <span aria-hidden="true">✓</span> <span>{a}</span>
            </li>
          ))}
          {failAtoms.map((a, idx) => (
            <li key={`f-${idx}`} className="pg-sr-atom-item is-fail">
              <span aria-hidden="true">✕</span> <span>{a}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {name === 'Code-Review' && crChipsHtml ? (
        <div className="pg-sr-card-voters">
          <span className="pg-sr-card-expr-label">Current votes:</span>{' '}
          <span dangerouslySetInnerHTML={{ __html: crChipsHtml }} />
        </div>
      ) : null}
      <div className="pg-sr-card-actions">
        <SrActionBtn clNum={clNum} sr={sr} />
      </div>
    </div>
  );
}

export function renderAiAnalysisStatusBannerHtml(
  num: number,
  patchSet: number,
  status: string,
  errorMsg?: string
): string {
  return renderReactToHtml(
    <AiAnalysisStatusBanner
      num={Number(num)}
      patchSet={Number(patchSet || 1)}
      status={status}
      errorMsg={errorMsg}
    />
  );
}

export function renderAiAlternativeCardHtml(
  num: number,
  alt: AiAlternativeItem,
  howItWorksHtml: string,
  tradeoffHtml = '',
  attachedItemsHtml = ''
): string {
  const cardHtml = renderReactToHtml(
    <AiAlternativeCard
      num={Number(num)}
      alt={alt || {}}
      howItWorksHtml={howItWorksHtml || ''}
      tradeoffHtml={tradeoffHtml || ''}
    />
  );
  return `${cardHtml}${attachedItemsHtml || ''}`;
}

export function renderCommitAiAnalysisPanelHtml(props: CommitAiAnalysisPanelProps): string {
  if (!props || !props.num) return '';
  return renderReactToHtml(<CommitAiAnalysisPanel {...props} />);
}

export function renderClSwarmBadgeHtml(sw?: SwarmStateEntry | null, elapsedSec = 0): string {
  if (!sw) return '';
  return renderReactToHtml(<ClSwarmBadge sw={sw} elapsedSec={elapsedSec} />);
}

export function renderSpecialistChipsHtml(
  agents?: SwarmAgentStatus[],
  isRunning = false,
  defaultSpecialists: SwarmAgentStatus[] = []
): string {
  return renderReactToHtml(
    <SpecialistChips
      agents={agents}
      isRunning={Boolean(isRunning)}
      defaultSpecialists={defaultSpecialists}
    />
  );
}

export function renderSwarmFindingsListHtml(
  clNum: number,
  findings?: SwarmFindingItem[]
): string {
  if (!Array.isArray(findings) || findings.length === 0) return '';
  return renderReactToHtml(<SwarmFindingsList clNum={Number(clNum)} findings={findings} />);
}

export function renderCommitSwarmPanelHtml(
  clNum: number,
  sw?: SwarmStateEntry | null,
  elapsedSec = 0,
  defaultSpecialists: SwarmAgentStatus[] = []
): string {
  if (!sw) return '';
  return renderReactToHtml(
    <CommitSwarmPanel
      clNum={Number(clNum)}
      sw={sw}
      elapsedSec={elapsedSec}
      defaultSpecialists={defaultSpecialists}
    />
  );
}

export function renderPgVoteBoxChipHtml(
  whoObj: { name?: string; short?: string; email?: string; is_bot?: boolean },
  val: number,
  labelName: string,
  avatarBg: string,
  initial: string
): string {
  const name = (whoObj && (whoObj.name || whoObj.short || whoObj.email)) || 'user';
  return renderReactToHtml(
    <VoteBoxChip
      name={name}
      val={Number(val || 0)}
      labelName={labelName || ''}
      avatarBg={avatarBg || '#5f6368'}
      initial={initial || 'U'}
    />
  );
}

export function renderPgSrAtomsHtml(sr: SubmitRequirementItem): string {
  if (!sr) return '';
  return renderReactToHtml(<SrAtomsList sr={sr} />);
}

export function renderPgSrActionBtnHtml(c: GerritChange | { number?: number }, sr: SubmitRequirementItem): string {
  const clNum = Number((c && c.number) || 0);
  return renderReactToHtml(<SrActionBtn clNum={clNum} sr={sr || {}} />);
}

export function renderPgSrDetailRowHtml(c: GerritChange | { number?: number }, sr: SubmitRequirementItem): string {
  const clNum = Number((c && c.number) || 0);
  return renderReactToHtml(<SrDetailRow clNum={clNum} sr={sr || {}} />);
}

export function renderPgCrSubmitRequirementRowHtml(props: CrSubmitRequirementRowProps): string {
  return renderReactToHtml(<CrSubmitRequirementRow {...props} />);
}

export function renderPgPvSubmitRequirementRowHtml(props: PvSubmitRequirementRowProps): string {
  return renderReactToHtml(<PvSubmitRequirementRow {...props} />);
}

export function renderPgTriggerVotesSectionHtml(props: TriggerVotesSectionProps): string {
  return renderReactToHtml(<TriggerVotesSection {...props} />);
}

export function renderPgSubmitRequirementsSectionHtml(props: SubmitRequirementsSectionProps): string {
  return renderReactToHtml(<SubmitRequirementsSection {...props} />);
}

export function renderPgSrAuditCardHtml(props: SrAuditCardProps): string {
  return renderReactToHtml(<SrAuditCard {...props} />);
}
