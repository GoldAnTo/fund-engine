# Research Run Human Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a real API-backed automatic-research, proposal-review, assessment-review, conclusion loop with durable background execution.

**Architecture:** `ResearchRun` remains the user-facing lifecycle record; a persistent worker claims queued runs and drives existing orchestration in bounded steps. Prototype UI owns start/cancel/case selection and proposal review; the case workbench owns assessment review.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite/PostgreSQL, React 18, TypeScript, Vitest, Playwright.

---

### Task 1: Lock orchestration isolation with regression tests

- [ ] Test and implement case-filtered extraction plus correct case-scoped evidence counting.

### Task 2: Make ResearchRun durable background work

- [ ] Test and implement queued start, worker claim/recovery, bounded cancellation, and worker CLI.

### Task 3: Build the proposal review UI seam

- [ ] Test and implement per-case run start/cancel, pending proposal decisions, and case/conclusion return links.

### Task 4: Align development modes and runtime tests

- [ ] Declare Node >=20, add explicit mock/live scripts, migrate old-page tests, and add a live SQLite API E2E.
