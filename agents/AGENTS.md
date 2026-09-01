# AEMU Agentic Development Workflow

This repository uses a multi-agent architecture to ensure high-quality engineering and
continuous improvement.

## Core Philosophy: Maximum Alignment

Our primary goal is **Maximum Alignment**, ensuring that the agent's actions are perfectly synchronized with human intent and architectural vision. This is distinct from—and superior to—merely maximizing autonomy.

1. **High Autonomy in Execution (The "How"):** The agent is 100% autonomous in managing process, style, testing, and Gerrit metadata. Human intervention here is considered **Instructional Debt** and must be eliminated.
2. **Deep Collaboration in Intent (The "What"):** For complex tasks with multiple implementation paths, the agent proactively seeks alignment. Human guidance here is **Strategic Value** and is welcomed to ensure the best architectural outcome.

The objective is **Frictionless Collaboration**: removing mechanical "noise" so human and agent can focus entirely on high-signal design and logic.

## Installation

To use this workflow from the source root, run the setup script once:

```bash
python3 hardware/google/aemu/setup_agents.py
```

## Specialized Agents

| Agent | Definition | Purpose |
| :--- | :--- | :--- |
| **Evolver** | [evolver.md](.gemini/agents/evolver.md) | Evolution engine; records interventions and hardens skills. |
| **Git Expert** | [git_expert.md](.gemini/agents/git_expert.md) | SCM Specialist; handles complex rebases and hygiene. |
| **Planner** | [planner.md](.gemini/agents/planner.md) | Architect; handles requirements and `DESIGN.md`. |
| **Critic** | [critic.md](.gemini/agents/critic.md) | Security & Efficiency reviewer; finds deep bugs. |
| **Deslopifier** | [deslopifier.md](.gemini/agents/deslopifier.md) | Phase 2 Specialist; decomposes raw working diffs into <=100L CL stacks. |
| **Reviewer** | [reviewer.md](.gemini/agents/reviewer.md) | Gatekeeper; handles style, license, docs, and diff audits. |
| **Debugger** | [debugger.md](.gemini/agents/debugger.md) | Investigation specialist; uses GDB and logging. |
| **Committer** | [committer.md](.gemini/agents/committer.md) | Release specialist; crafts commit messages. |
| **Emu Main Next Engineer** | [emu_main_next_engineer.md](.gemini/agents/emu_main_next_engineer.md) | Domain Specialist; implements features/fixes in `emu-main-next`. |

## The Development Lifecycle (Orchestration Protocol)

This workflow ensures high-quality delivery through specialized hand-offs.

1.  **Telemetry Setup:**
    *   Activate the **Evolver** to record telemetry and intervention logs.
2.  **Architecture & Design:**
    *   **Context Discovery:** If `emu_main_next_documenter` is available, task it to map the relevant components and existing flows. If not, use `grep_search` and `glob` manually.
    *   **Design:** Activate the **Planner** to design the solution and draft a `DESIGN.md`.
    *   **Engagement Mode Selection:** The human selects either *Autonomous Mode* or *Collaborative Mode*.
3.  **Design Review:**
    *   Activate the **Critic** to scrutinize the `DESIGN.md`.
4.  **Implementation (Phase 1: Holistic Prototyping):**
    *   **Delegated Path:** If `emu_main_next_engineer` is available, delegate the implementation task entirely. They will handle the TDD loop and `test_enforcer` coordination in a working commit/branch.
    *   **Default Path:** If no Specialist exists, perform the implementation yourself following the TDD loop (Red/Green/Refactor). Confirm every fix with a new or updated unit test.
5.  **De-sloppification & Stack Slicing (Phase 2: Review Preparation):**
    *   Activate the **Deslopifier** to isolate `[PREFACTOR]` commits, decompose the working prototype into atomic slices ($\le 100\text{L}$ logic), sanitize comments, and localize formatting.
6.  **Quality Audit:**
    *   Activate the **Reviewer** to perform a final audit of style, documentation, and diffs.
7.  **Submission:**
    *   Activate the **Committer** to prepare semantic commit messages and perform the Gerrit upload (`repo upload`).
8.  **Autonomy Audit (Post-Mortem):**
    *   Upon approval, the **Evolver** analyzes the session log to harden skills.

## The Organizational Hierarchy

To use this system effectively, understand the chain of command:

| Level | Role | Responsibilities |
| :--- | :--- | :--- |
| **Executive** | CEO / Founder | Sets vision, defines project culture. |
| **Management** | Director | **Zero-Repeat Mandate:** Orchestrates specialists and logs friction. |
| **Specialist** | Staff Eng. | Performs execution: Planning, Security Audits, Coding, etc. |

## The Four Pillars of Reviewability

Every change produced by this team must adhere to these standards:

1.  **Semantic Atomicity:** One commit, one logical change. Never bundle unrelated features.
2.  **No-Surprise Diffs:** Every line in a diff must be a direct servant of the commit's subject.
3.  **Skeleton-First Progression:** Large features delivered in a layered stack.
4.  **Proactive Justification:** Explain *why* specific choices were made.

## Continuous Evolution: The Duo

The **Evolver** is the heart of the system's growth, focused on eliminating **Instructional Debt** while preserving **Strategic Alignment**.

1.  **Job 1 (The Sensor):** The **Evolver** records every human interaction. It distinguishes between:
    *   **Instructional Debt:** Corrections to process, style, or known rules. These are targets for automation.
    *   **Collaborative Guidance:** Strategic intent, architectural trade-offs, or new context. These are targets for knowledge capture.
2.  **Job 2 (The Actuator):** Once approved, the **Evolver** analyzes the log to harden skills (eliminating debt) and update documentation/references (capturing guidance).
