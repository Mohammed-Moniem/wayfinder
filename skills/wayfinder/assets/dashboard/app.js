(() => {
  "use strict";

  const API_ENDPOINT = "./api/state";
  const SESSION_ENDPOINT = "./api/session";
  const CHOICE_ENDPOINT = "./api/intake/choice";
  const ANSWER_ENDPOINT = "./api/intake/answer";
  const ROUTES = ["overview", "map", "decisions", "evidence", "assumptions", "invariants", "checkpoints"];
  const COMPARISON_TRADEOFF_FIELDS = [
    ["mvp_speed", "MVP speed"],
    ["scale_beyond_mvp", "Growth beyond MVP"],
    ["reliability", "Reliability"],
    ["efficiency", "Efficiency"],
    ["cost", "Cost"],
    ["complexity", "Complexity"],
    ["lock_in", "Lock-in"],
    ["security_privacy", "Security and privacy"],
    ["team_fit", "Team fit"],
    ["reversibility", "Reversibility"],
    ["schedule", "Schedule"],
    ["cost_certainty", "Cost certainty"],
    ["safety_quality", "Safety and quality"],
    ["coordination", "Coordination"],
    ["flexibility", "Flexibility"],
    ["regulatory_dependency", "Regulatory dependency"],
    ["close_speed", "Close speed"],
    ["auditability", "Auditability"],
    ["controls", "Controls"],
    ["scalability", "Scalability"],
    ["reconciliation_effort", "Reconciliation effort"],
    ["speed", "Speed"],
    ["governance", "Governance"],
  ];
  const PHASE_LABELS = [
    "Frame destination",
    "Resolve route",
    "Prove route",
    "Ready for execution",
    "Delivery & revalidation",
  ];
  const STATUS_ORDER = ["resolved", "actionable", "waiting", "blocked", "claimed", "gates"];
  const NODE_WIDTH = 180;
  const NODE_HEIGHT = 78;
  const COLUMN_GAP = 84;
  const ROW_GAP = 42;
  const LAYOUT_PADDING_X = 88;
  const LAYOUT_PADDING_Y = 54;
  const MIN_ZOOM = 0.5;
  const MAX_ZOOM = 4;
  const FOCUSED_NODE_HEIGHT = 72;

  const byId = (id) => document.getElementById(id);
  const dom = {
    shell: byId("app-shell"),
    sideRail: byId("side-rail"),
    mainColumn: byId("main-column"),
    projectHeader: document.querySelector(".project-header"),
    phaseRoute: document.querySelector(".phase-route"),
    statusFooter: byId("status-footer"),
    workspace: byId("workspace"),
    routeMain: byId("route-main"),
    routeToolbar: byId("route-toolbar"),
    skipLink: byId("skip-link"),
    projectTitle: byId("project-title"),
    projectState: byId("project-state-label"),
    refresh: byId("refresh-state"),
    retry: byId("retry-state"),
    phaseList: byId("phase-route-list"),
    checkpointSummary: byId("checkpoint-summary"),
    checkpointLabel: byId("checkpoint-label"),
    checkpointRecommendation: byId("checkpoint-recommendation"),
    interactionMode: byId("interaction-mode"),
    interactionModeLabel: byId("interaction-mode-label"),
    mapButton: byId("map-view-button"),
    listButton: byId("list-view-button"),
    search: byId("decision-search"),
    statusFilters: byId("status-filters"),
    kindFilter: byId("kind-filter"),
    banner: byId("state-banner"),
    bannerTitle: byId("state-banner-title"),
    bannerMessage: byId("state-banner-message"),
    mapSurface: byId("map-surface"),
    listSurface: byId("list-surface"),
    graph: byId("dependency-graph"),
    graphViewport: byId("graph-viewport"),
    graphEdges: byId("graph-edges"),
    graphNodes: byId("graph-nodes"),
    graphMessage: byId("graph-message"),
    graphMessageTitle: byId("graph-message-title"),
    graphMessageCopy: byId("graph-message-copy"),
    listBody: byId("decision-list-body"),
    listMessage: byId("list-message"),
    zoomIn: byId("zoom-in"),
    zoomOut: byId("zoom-out"),
    fitMap: byId("fit-map"),
    focusMap: byId("focus-map"),
    graphModeLabel: byId("graph-mode-label"),
    inspector: byId("inspector"),
    inspectorEmpty: byId("inspector-empty"),
    inspectorEmptyTitle: byId("inspector-empty-title"),
    inspectorEmptyCopy: byId("inspector-empty-copy"),
    inspectorContent: byId("inspector-content"),
    closeInspector: byId("close-inspector"),
    selectedNodeId: byId("selected-node-id"),
    selectedNodeTitle: byId("selected-node-title"),
    selectedNodeStatus: byId("selected-node-status"),
    selectedNodeQuestion: byId("selected-node-question"),
    selectedNodeRecommendation: byId("selected-node-recommendation"),
    selectedNodeConsequence: byId("selected-node-consequence"),
    selectedNodeUnlocks: byId("selected-node-unlocks"),
    inspectorProvenance: byId("inspector-provenance"),
    openDecision: byId("open-decision"),
    activityList: byId("activity-list"),
    activityEmpty: byId("activity-empty"),
    activityStrip: byId("activity-strip"),
    viewAllActivity: byId("view-all-activity"),
    statusCounts: byId("status-counts"),
    footerHealth: byId("footer-health"),
    footerHealthLabel: byId("footer-health-label"),
    totalCount: byId("total-count"),
    collapseRail: byId("collapse-rail"),
    mobileMenu: byId("mobile-menu"),
    closeMobileNav: byId("close-mobile-nav"),
    railMapLink: byId("rail-map-link"),
    railDecisionsLink: byId("rail-decisions-link"),
    overviewUpdated: byId("overview-updated"),
    overviewDestination: byId("overview-destination"),
    overviewRecommendationCard: byId("overview-recommendation-card"),
    overviewRecommendation: byId("overview-recommendation"),
    overviewRecommendationReason: byId("overview-recommendation-reason"),
    overviewNextLink: byId("overview-next-link"),
    overviewMetrics: byId("overview-metrics"),
    overviewPhase: byId("overview-phase"),
    overviewPhaseDescription: byId("overview-phase-description"),
    overviewPhaseProgress: byId("overview-phase-progress"),
    overviewHealth: byId("overview-health"),
    overviewHealthDetail: byId("overview-health-detail"),
    overviewHealthList: byId("overview-health-list"),
    intakeProgress: byId("intake-progress"),
    primaryWorkstream: byId("primary-workstream"),
    secondaryWorkstreams: byId("secondary-workstreams"),
    secondaryWorkstreamsEmpty: byId("secondary-workstreams-empty"),
    comparisonLevel: byId("comparison-level"),
    comparisonTitle: byId("comparison-title"),
    comparisonExplanation: byId("comparison-explanation"),
    comparisonList: byId("comparison-list"),
    baselineEffort: byId("baseline-effort"),
    baselineRevision: byId("baseline-revision"),
    baselineHandoff: byId("baseline-handoff"),
    baselineList: byId("baseline-list"),
    decisionsSummary: byId("decisions-summary"),
    openIntake: byId("open-intake"),
    evidenceCount: byId("evidence-count"),
    evidenceList: byId("evidence-list"),
    evidenceEmpty: byId("evidence-empty"),
    exitStatus: byId("exit-status"),
    exitHeading: byId("exit-heading"),
    exitList: byId("exit-list"),
    assumptionsList: byId("assumptions-list"),
    assumptionsEmpty: byId("assumptions-empty"),
    invariantsList: byId("invariants-list"),
    invariantsEmpty: byId("invariants-empty"),
    checkpointCallout: byId("checkpoint-callout"),
    checkpointCalloutLabel: byId("checkpoint-callout-label"),
    checkpointCalloutReason: byId("checkpoint-callout-reason"),
    checkpointCards: byId("checkpoint-cards"),
    milestoneProgress: byId("milestone-progress"),
    milestoneList: byId("milestone-list"),
    milestoneEmpty: byId("milestone-empty"),
    recordingPanel: byId("recording-panel"),
    recordingModeBadge: byId("recording-mode-badge"),
    recordingModeNote: byId("recording-mode-note"),
    recordingForm: byId("decision-recording-form"),
    recordingQuestion: byId("recording-question"),
    recordingWhy: byId("recording-why"),
    recordingOptionsGroup: byId("recording-options-group"),
    recordingOptions: byId("recording-options"),
    recordingAnswerControl: byId("recording-answer-control"),
    recordingAnswer: byId("recording-answer"),
    recordingAnswerHint: byId("recording-answer-hint"),
    recordingRevision: byId("recording-revision"),
    recordingFeedback: byId("recording-feedback"),
    recordAnswer: byId("record-answer"),
    toast: byId("toast"),
  };
  const SVG_NS = dom.graph.namespaceURI;
  const mobileNavigationQuery = window.matchMedia("(max-width: 760px)");
  const modalInspectorQuery = window.matchMedia("(max-width: 900px)");

  const ui = {
    payload: null,
    session: { mode: "read-only", csrfToken: "", recordable: false },
    route: "overview",
    query: "",
    status: "all",
    kind: "all",
    view: "map",
    selectedId: null,
    selectedPhaseId: null,
    activityFilter: "all",
    showAllActivity: false,
    zoom: 1,
    offsetX: 0,
    offsetY: 0,
    graphWidth: 1000,
    graphHeight: 600,
    graphPositions: new Map(),
    graphHasFramed: false,
    graphMode: "preparing",
    visibleNodeIds: [],
    pan: null,
    toastTimer: null,
    submitting: false,
    pendingFocusTarget: null,
    inspectorDismissedFor: null,
    inspectorOpener: null,
  };

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function asText(value, fallback = "") {
    let raw = "";
    if (typeof value === "string") raw = value;
    if (typeof value === "number" || typeof value === "boolean") raw = String(value);
    const clean = raw
      .replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]+/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
    return clean || fallback;
  }

  function asBoolean(value, fallback = false) {
    return typeof value === "boolean" ? value : fallback;
  }

  function normalizedToken(value, fallback = "unknown") {
    const token = asText(value, fallback).toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
    return token || fallback;
  }

  function plainLabel(value) {
    const token = asText(value);
    const mapped = COMPARISON_TRADEOFF_FIELDS.find(([key]) => key === token)?.[1];
    return mapped || token.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function compactText(value, fallback, limit = 700) {
    const result = asText(value, fallback);
    return result.length > limit ? `${result.slice(0, limit - 1)}…` : result;
  }

  function referenceId(value) {
    if (value && typeof value === "object") {
      return asText(value.id || value.slug || value.node_id);
    }
    return asText(value);
  }

  function statusGroup(status) {
    const token = normalizedToken(status);
    if (["resolved", "complete", "completed", "accepted-risk", "closed"].includes(token)) {
      return "resolved";
    }
    if (["actionable", "open", "reopened", "ready", "due"].includes(token)) {
      return "actionable";
    }
    if (["claimed", "in-progress", "in_progress", "active"].includes(token)) {
      return "claimed";
    }
    if (["blocked", "failed", "invalid"].includes(token)) {
      return "blocked";
    }
    if (["waiting", "pending", "upcoming", "not-started", "not_started"].includes(token)) {
      return "waiting";
    }
    return token;
  }

  function statusLabel(status) {
    const group = statusGroup(status);
    const labels = {
      resolved: "Resolved",
      actionable: "Actionable",
      waiting: "Waiting",
      blocked: "Blocked",
      claimed: "Claimed",
      complete: "Complete",
      unknown: "Unknown",
    };
    return labels[group] || group.replace(/-/g, " ").replace(/^./, (letter) => letter.toUpperCase());
  }

  function gateStatusLabel(status) {
    const token = normalizedToken(status);
    const labels = {
      open: "Pending",
      reopened: "Pending",
      ready: "Pending",
      defined: "Defined",
      pending: "Pending",
      running: "Evaluating",
      claimed: "Evaluating",
      evaluating: "Evaluating",
      resolved: "Passed",
      passed: "Passed",
      failed: "Failed",
      stale: "Stale",
      waived: "Waived",
      superseded: "Superseded",
    };
    return labels[token] || statusLabel(token);
  }

  function nodeStatusLabel(node) {
    return node.kind === "gate" ? gateStatusLabel(node.status) : statusLabel(node.viewStatus || node.status);
  }

  function nodeStatusGroup(node) {
    if (node.kind !== "gate") return node.viewStatus ? normalizedToken(node.viewStatus) : statusGroup(node.status);
    const token = normalizedToken(node.status);
    if (["passed", "waived", "resolved", "superseded"].includes(token)) return "resolved";
    if (["blocked", "failed", "stale"].includes(token)) return "blocked";
    if (["evaluating", "running", "claimed"].includes(token)) return "claimed";
    return "waiting";
  }

  function projectStatusLabel(status) {
    const token = normalizedToken(status, "in-progress");
    if (["in-progress", "in_progress", "active", "claimed"].includes(token)) return "In progress";
    if (["complete", "completed", "resolved"].includes(token)) return "Complete";
    if (token === "ready-for-spec") return "Ready for execution";
    if (["paused", "blocked"].includes(token)) return token === "paused" ? "Paused" : "Blocked";
    return statusLabel(token);
  }

  function statusIconName(status, kind = "decision") {
    if (normalizedToken(kind) === "gate") {
      return "lock";
    }
    const group = statusGroup(status);
    if (group === "resolved") return "check";
    if (group === "blocked") return "minus";
    if (group === "waiting") return "clock";
    if (group === "claimed") return "dot";
    if (group === "actionable") return "alert";
    return "dot";
  }

  function createElement(tag, className, textValue) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (textValue !== undefined) element.textContent = asText(textValue);
    return element;
  }

  function safeHttpsUrl(value) {
    const textValue = asText(value);
    if (!textValue) return "";
    try {
      const url = new URL(textValue);
      if (url.protocol !== "https:" || url.username || url.password) return "";
      return url.href;
    } catch (_error) {
      return "";
    }
  }

  function createSourceReference(value) {
    const reference = compactText(value, "", 500);
    const safeUrl = safeHttpsUrl(reference);
    if (!safeUrl) return createElement("span", "comparison-source", reference);
    const link = createElement("a", "comparison-source", reference);
    link.href = safeUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }

  function createSvgElement(tag, attributes = {}) {
    const element = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([name, value]) => {
      if (value !== undefined && value !== null) {
        element.setAttribute(name, String(value));
      }
    });
    return element;
  }

  function appendStatusIcon(parent, status, kind = "decision", svgContext = false) {
    const icon = svgContext
      ? createSvgElement("g", { class: "status-symbol", "aria-hidden": "true" })
      : createElement("span", "status-symbol");
    if (!svgContext) icon.setAttribute("aria-hidden", "true");
    const iconName = statusIconName(status, kind);

    const addSvgShape = (tag, attributes) => {
      const shape = createSvgElement(tag, attributes);
      icon.append(shape);
    };

    if (!svgContext) {
      const svg = createSvgElement("svg", { viewBox: "0 0 16 16", width: "16", height: "16" });
      svg.setAttribute("aria-hidden", "true");
      if (iconName === "check") {
        svg.append(createSvgElement("circle", { cx: 8, cy: 8, r: 6 }));
        svg.append(createSvgElement("path", { d: "m5.2 8 1.8 1.8 3.8-4" }));
      } else if (iconName === "minus") {
        svg.append(createSvgElement("circle", { cx: 8, cy: 8, r: 6 }));
        svg.append(createSvgElement("path", { d: "M5 8h6" }));
      } else if (iconName === "clock") {
        svg.append(createSvgElement("circle", { cx: 8, cy: 8, r: 6 }));
        svg.append(createSvgElement("path", { d: "M8 4.5V8l2.3 1.4" }));
      } else if (iconName === "lock") {
        svg.append(createSvgElement("rect", { x: 4, y: 7, width: 8, height: 6, rx: 1 }));
        svg.append(createSvgElement("path", { d: "M5.7 7V5.5a2.3 2.3 0 0 1 4.6 0V7" }));
      } else if (iconName === "alert") {
        svg.append(createSvgElement("circle", { cx: 8, cy: 8, r: 6 }));
        svg.append(createSvgElement("path", { d: "M8 4.7v4.1M8 11.2h.01" }));
      } else {
        svg.append(createSvgElement("circle", { cx: 8, cy: 8, r: 6 }));
        svg.append(createSvgElement("circle", { cx: 8, cy: 8, r: 1.7, fill: "currentColor", stroke: "none" }));
      }
      icon.append(svg);
      parent.append(icon);
      return icon;
    }

    if (iconName === "check") {
      addSvgShape("circle", { cx: 0, cy: 0, r: 6 });
      addSvgShape("path", { d: "M-3 0 -1 2.2 3.4 -2.5" });
    } else if (iconName === "minus") {
      addSvgShape("circle", { cx: 0, cy: 0, r: 6 });
      addSvgShape("path", { d: "M-3 0H3" });
    } else if (iconName === "clock") {
      addSvgShape("circle", { cx: 0, cy: 0, r: 6 });
      addSvgShape("path", { d: "M0-3.5V0L2.4 1.5" });
    } else if (iconName === "lock") {
      addSvgShape("rect", { x: -4, y: -1, width: 8, height: 6, rx: 1 });
      addSvgShape("path", { d: "M-2.5-1v-1.5a2.5 2.5 0 0 1 5 0V-1" });
    } else if (iconName === "alert") {
      addSvgShape("circle", { cx: 0, cy: 0, r: 6 });
      addSvgShape("path", { d: "M0-3.5V1M0 3.5h.01" });
    } else {
      addSvgShape("circle", { cx: 0, cy: 0, r: 6 });
      addSvgShape("circle", { cx: 0, cy: 0, r: 1.6, fill: "currentColor", stroke: "none" });
    }
    parent.append(icon);
    return icon;
  }

  function normalizePhase(rawPhase, index) {
    const phase = asObject(rawPhase);
    const checkpoint = asObject(phase.checkpoint);
    return {
      id: asText(phase.id, `phase-${index + 1}`),
      label: PHASE_LABELS[index],
      state: normalizedToken(phase.state, "upcoming"),
      description: compactText(phase.description, "No phase description is recorded."),
      checkpoint: {
        id: asText(checkpoint.id),
        label: compactText(checkpoint.label, "No checkpoint recorded.", 180),
        state: normalizedToken(checkpoint.state, "pending"),
        recommendedRun: asBoolean(checkpoint.recommended_run),
        reason: compactText(checkpoint.reason, "No run reason is recorded.", 380),
      },
    };
  }

  function normalizeNode(rawNode) {
    const node = asObject(rawNode);
    const id = asText(node.id);
    const kind = normalizedToken(node.kind, "decision");
    const unlocks = asArray(node.unlocks).map((item) => {
      if (item && typeof item === "object") {
        return compactText(item.title || item.label || item.id, "", 180);
      }
      return compactText(item, "", 180);
    }).filter(Boolean);
    return {
      id,
      kind,
      title: compactText(node.title, id || "Untitled decision", 180),
      question: compactText(node.question, "No exact question is recorded."),
      status: normalizedToken(node.status, "unknown"),
      viewStatus: "",
      autonomy: normalizedToken(node.autonomy, "unknown"),
      responsibleParty: compactText(node.responsible_party, "Not recorded", 120),
      nextActor: compactText(node.next_actor, "Not recorded", 120),
      phase: referenceId(node.phase),
      destinationBlocking: asBoolean(node.destination_blocking),
      requires: asArray(node.requires).map(referenceId).filter(Boolean),
      revalidates: asArray(node.revalidates).map(referenceId).filter(Boolean),
      informs: asArray(node.informs).map(referenceId).filter(Boolean),
      gates: asArray(node.gates).map(referenceId).filter(Boolean),
      summary: compactText(node.summary, "No summary is recorded."),
      recommendation: compactText(node.recommendation, ""),
      consequence: compactText(node.consequence_of_waiting, "No consequence of waiting is recorded."),
      waitingReason: compactText(node.waiting_reason, "", 260),
      unlocks,
      evidence: asArray(node.evidence),
      path: compactText(node.path, "", 400),
      revision: Number.isSafeInteger(node.revision) && node.revision >= 0 ? node.revision : null,
    };
  }

  function normalizeEdge(rawEdge) {
    const edge = asObject(rawEdge);
    return {
      source: referenceId(edge.source),
      target: referenceId(edge.target),
      type: normalizedToken(edge.type, "requires"),
    };
  }

  function normalizeActivity(rawActivity, index) {
    const activity = asObject(rawActivity);
    const type = normalizedToken(activity.type || activity.kind, "update");
    return {
      id: asText(activity.id, `activity-${index}`),
      type: type.includes("invalid") ? "invalidation" : "update",
      timestamp: asText(activity.timestamp || activity.occurred_at || activity.at || activity.date),
      nodeId: referenceId(activity.node_id || activity.node || activity.decision_id),
      message: compactText(activity.message || activity.title || activity.description, "Recorded project update.", 500),
      actor: compactText(activity.actor || activity.by, "", 100),
    };
  }

  function normalizeEvidence(rawEvidence) {
    const evidence = asObject(rawEvidence);
    return {
      id: asText(evidence.id),
      title: compactText(evidence.title, asText(evidence.id, "Evidence"), 220),
      method: compactText(evidence.method, "Method not recorded", 180),
      observedAt: asText(evidence.observed_at),
      subjectRevision: compactText(evidence.subject_revision, "Not recorded", 120),
      source: compactText(evidence.source, "Source not recorded", 260),
      sourceType: normalizedToken(evidence.source_type, "unknown"),
      collector: compactText(evidence.collector, "Not recorded", 120),
      basis: compactText(evidence.basis, "", 400),
      confidence: normalizedToken(evidence.confidence, "unknown"),
      sensitivity: normalizedToken(evidence.sensitivity, "unknown"),
      revalidateWhen: compactText(evidence.revalidate_when, "No revalidation trigger recorded", 320),
      freshness: normalizedToken(evidence.freshness, "unknown"),
      conclusion: compactText(evidence.conclusion, "No conclusion recorded", 600),
    };
  }

  function normalizeAssumption(rawAssumption) {
    const assumption = asObject(rawAssumption);
    return {
      id: asText(assumption.id),
      summary: compactText(assumption.summary, "Assumption summary not recorded", 600),
      impact: compactText(assumption.impact, "Impact not recorded", 400),
      confidence: normalizedToken(assumption.confidence, "unknown"),
      status: normalizedToken(assumption.status, "unknown"),
      destinationBlocking: asBoolean(assumption.destination_blocking),
      affects: asArray(assumption.affects).map(referenceId).filter(Boolean),
      evidence: asArray(assumption.evidence).map(referenceId).filter(Boolean),
      revalidateWhen: compactText(assumption.revalidate_when, "No revalidation trigger recorded", 320),
    };
  }

  function normalizeInvariant(rawInvariant) {
    const invariant = asObject(rawInvariant);
    return {
      id: asText(invariant.id),
      invariant: compactText(invariant.invariant, "Invariant statement not recorded", 600),
      status: normalizedToken(invariant.status, "unknown"),
      scope: compactText(invariant.scope, "Scope not recorded", 260),
      rationale: compactText(invariant.rationale, "Rationale not recorded", 500),
      enforcement: compactText(invariant.enforcement, "Enforcement not recorded", 400),
      evidence: asArray(invariant.evidence).map(referenceId).filter(Boolean),
      responsibleParty: compactText(invariant.responsible_party, "Not recorded", 160),
      revalidateWhen: compactText(invariant.revalidate_when, "No revalidation trigger recorded", 320),
    };
  }

  function normalizeMilestone(rawMilestone) {
    const milestone = asObject(rawMilestone);
    return {
      id: asText(milestone.id),
      label: compactText(milestone.label, asText(milestone.id, "Milestone"), 220),
      phaseId: referenceId(milestone.phase_id),
      state: normalizedToken(milestone.state, "upcoming"),
      criteria: compactText(milestone.criteria, "Criteria not recorded", 500),
    };
  }

  function normalizeIntake(rawIntake) {
    const intake = asObject(rawIntake);
    const rawProgress = asObject(intake.progress);
    const rawDomain = asObject(intake.domain);
    const rawQuestion = asObject(intake.current_question);
    const options = asArray(rawQuestion.options).map((rawOption) => {
      const option = asObject(rawOption);
      return {
        id: asText(option.id),
        label: compactText(option.label || option.title, asText(option.id, "Option"), 220),
        description: compactText(option.description || option.detail, "", 500),
      };
    }).filter((option) => option.id);
    const rawRevision = intake.revision;
    const revision = Number.isSafeInteger(rawRevision) && rawRevision >= 0 ? rawRevision : null;
    const rawMaximum = rawQuestion.max_length;
    const maxLength = Number.isSafeInteger(rawMaximum) && rawMaximum > 0
      ? Math.min(rawMaximum, 4000)
      : 4000;
    return {
      state: normalizedToken(intake.state, "unknown"),
      status: normalizedToken(intake.status, "unknown"),
      revision,
      progress: {
        answered: Number.isSafeInteger(rawProgress.answered) && rawProgress.answered >= 0 ? rawProgress.answered : 0,
        total: Number.isSafeInteger(rawProgress.total) && rawProgress.total >= 0 ? rawProgress.total : null,
        percent: Number.isSafeInteger(rawProgress.percent) ? Math.min(100, Math.max(0, rawProgress.percent)) : 0,
      },
      domain: {
        primary: asText(rawDomain.primary_domain || rawDomain.selected),
        proposed: asText(rawDomain.proposed),
        confidence: normalizedToken(rawDomain.confidence, "unknown"),
        secondaryWorkstreams: asArray(rawDomain.secondary_workstreams).map((rawWorkstream) => {
          const workstream = asObject(rawWorkstream);
          return {
            id: asText(workstream.id),
            domain: asText(workstream.domain),
            outcome: compactText(workstream.outcome, "Outcome not recorded", 600),
            authority: compactText(workstream.authority, "Authority not recorded", 120),
            decisions: asArray(workstream.decision_ids).map(referenceId).filter(Boolean),
          };
        }).filter((workstream) => workstream.id),
      },
      comparisons: asArray(intake.comparisons).map((rawComparison) => {
        const comparison = asObject(rawComparison);
        const explicitLevel = normalizedToken(comparison.comparison_level || comparison.level || comparison.kind, "strategy");
        return {
          id: asText(comparison.id),
          title: compactText(comparison.title, "Route strategy comparison", 220),
          level: ["named-technology", "named-technologies", "named_technology", "named_technologies", "vendor", "technology"].includes(explicitLevel) ? "named-technology" : "strategy",
          kind: explicitLevel,
          criteria: asArray(comparison.criteria).map((criterion) => compactText(plainLabel(criterion), "", 80)).filter(Boolean),
          recommendedOption: asText(comparison.recommended_option),
          selectedOption: asText(comparison.selected_option),
          rationale: compactText(comparison.recommendation_rationale, "", 500),
          options: asArray(comparison.options).map((rawOption) => {
            const option = asObject(rawOption);
            const name = compactText(option.label || option.name, asText(option.id, "Option"), 180);
            const version = compactText(option.version_or_constraint, "", 100);
            return {
              id: asText(option.id),
              label: version ? `${name} · ${version}` : name,
              summary: compactText(option.summary || option.rationale, "", 500),
              recommendation: asBoolean(option.recommendation),
              tradeoffs: COMPARISON_TRADEOFF_FIELDS.map(([key, label]) => ({
                label,
                value: compactText(option[key], "", 420),
              })).filter((item) => item.value),
              evidenceRefs: [...new Set([
                ...asArray(option.evidence_refs),
                ...asArray(option.primary_sources),
              ].map((item) => compactText(item, "", 500)).filter(Boolean))].slice(0, 16),
            };
          }).filter((option) => option.id),
        };
      }).filter((comparison) => comparison.id),
      currentQuestion: rawQuestion.id ? {
        id: asText(rawQuestion.id),
        decisionId: referenceId(rawQuestion.decision_id),
        prompt: compactText(rawQuestion.prompt, "Current intake question", 1000),
        why: compactText(rawQuestion.why, "", 1000),
        answerType: normalizedToken(rawQuestion.answer_type, options.length ? "choice" : "text"),
        options,
        humanChoiceRequired: asBoolean(rawQuestion.human_choice_required, true),
        maxLength,
      } : null,
    };
  }

  function computeCounts(nodes) {
    const counts = { resolved: 0, actionable: 0, waiting: 0, blocked: 0, claimed: 0, gates: 0, decisions: 0, total: nodes.length };
    nodes.forEach((node) => {
      if (node.kind === "gate") {
        counts.gates += 1;
      } else {
        counts.decisions += 1;
        const group = nodeStatusGroup(node);
        if (Object.hasOwn(counts, group)) counts[group] += 1;
      }
    });
    return counts;
  }

  function normalizePayload(rawPayload) {
    const payload = asObject(rawPayload);
    const rawProject = asObject(payload.project);
    const rawPhases = asArray(payload.phases);
    const nodes = asArray(payload.nodes).map(normalizeNode).filter((node) => node.id);
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges = asArray(payload.edges)
      .map(normalizeEdge)
      .filter((edge) => edge.source && edge.target && nodeIds.has(edge.source) && nodeIds.has(edge.target));
    const rawViews = asObject(payload.views);
    const viewMembership = new Map();
    for (const group of ["actionable", "waiting", "blocked", "claimed"]) {
      asArray(rawViews[group]).map(referenceId).filter(Boolean).forEach((id) => viewMembership.set(id, group));
    }
    nodes.forEach((node) => {
      if (node.kind !== "gate") node.viewStatus = viewMembership.get(node.id) || statusGroup(node.status);
      if (!node.recommendation) {
        node.recommendation = node.viewStatus === "waiting" && node.waitingReason
          ? `${node.waitingReason.replace(/^waiting for\s+/i, "Wait for ")}.`
          : "No recommendation is recorded.";
      }
    });
    const calculatedCounts = computeCounts(nodes);
    const rawRecommendation = asObject(payload.run_recommendation);
    const recommendationLevel = normalizedToken(rawRecommendation.level, "none");
    const rawHealth = asObject(payload.health);
    const rawExit = asObject(payload.exit);
    const rawBaseline = asObject(payload.implementation_baseline);

    const phases = PHASE_LABELS.map((_, index) => normalizePhase(rawPhases[index], index));
    return {
      schemaVersion: asText(payload.schema_version, "unknown"),
      project: {
        title: compactText(rawProject.title, "Wayfinder project", 180),
        slug: asText(rawProject.slug),
        status: normalizedToken(rawProject.status, "in-progress"),
        destination: compactText(rawProject.destination, "No destination is recorded."),
        local: asBoolean(rawProject.local, true),
        readOnly: asBoolean(rawProject.read_only, true),
        lastUpdated: asText(rawProject.last_updated),
      },
      phases,
      currentPhase: referenceId(payload.current_phase),
      currentCheckpoint: referenceId(payload.current_checkpoint),
      runRecommendation: {
        level: recommendationLevel,
        recommended: asBoolean(rawRecommendation.recommended, ["now", "required", "checkpoint"].includes(recommendationLevel)),
        label: compactText(rawRecommendation.label, "No Wayfinder run is currently recommended.", 180),
        reason: compactText(rawRecommendation.reason, "No recommendation reason is recorded.", 500),
        trigger: compactText(rawRecommendation.trigger, "", 220),
      },
      // Counts are derived from the exact normalized nodes/views rendered below.
      // This prevents a stale or older payload summary from disagreeing with the map.
      counts: calculatedCounts,
      nodes,
      edges,
      views: rawViews,
      activity: asArray(payload.activity).map(normalizeActivity),
      evidence: asArray(payload.evidence).map(normalizeEvidence).filter((item) => item.id),
      assumptions: asArray(payload.assumptions).map(normalizeAssumption).filter((item) => item.id),
      invariants: asArray(payload.invariants).map(normalizeInvariant).filter((item) => item.id),
      milestones: asArray(payload.milestones).map(normalizeMilestone).filter((item) => item.id),
      intake: normalizeIntake(payload.intake),
      implementationBaseline: {
        effortId: asText(rawBaseline.effort_id),
        destinationRevision: Number.isSafeInteger(rawBaseline.destination_revision) ? rawBaseline.destination_revision : null,
        intakeRevision: Number.isSafeInteger(rawBaseline.intake_revision) ? rawBaseline.intake_revision : 0,
        primaryDomain: asText(rawBaseline.primary_domain),
        manifestHash: typeof rawBaseline.manifest_hash === "string" && /^[a-f0-9]{64}$/i.test(rawBaseline.manifest_hash)
          ? rawBaseline.manifest_hash.toLowerCase()
          : "",
        applicableDecisions: asArray(rawBaseline.applicable_decisions).map((rawDecision) => {
          const decision = asObject(rawDecision);
          return {
            id: asText(decision.id),
            revision: Number.isSafeInteger(decision.revision) ? decision.revision : null,
            status: normalizedToken(decision.status, "unknown"),
          };
        }).filter((decision) => decision.id),
      },
      exit: {
        planningExitReady: asBoolean(rawExit.planning_exit_ready, asBoolean(rawExit.pre_spec_ready)),
        complete: asBoolean(rawExit.complete),
        unresolvedDestinationDecisions: asArray(rawExit.unresolved_destination_decisions).map(referenceId).filter(Boolean),
        pendingDeliveryGates: asArray(rawExit.pending_delivery_gates).map(referenceId).filter(Boolean),
        highImpactOpenAssumptions: asArray(rawExit.high_impact_open_assumptions).map(referenceId).filter(Boolean),
        unformulatedFog: asArray(rawExit.unformulated_fog).map(referenceId).filter(Boolean),
        remainingNonblockingUnknowns: asArray(rawExit.remaining_nonblocking_unknowns).map(referenceId).filter(Boolean),
        executionHandoff: compactText(rawExit.execution_handoff, "Execution planning and controls", 300),
      },
      health: {
        status: normalizedToken(rawHealth.status, "healthy"),
        issues: asArray(rawHealth.issues),
        warnings: asArray(rawHealth.warnings),
      },
    };
  }

  function renderSurfaceState(name, title, message = "") {
    dom.banner.dataset.state = name;
    dom.bannerTitle.textContent = title;
    dom.bannerMessage.textContent = message;
    dom.retry.hidden = name !== "error";
    dom.workspace.setAttribute("aria-busy", name === "loading" ? "true" : "false");
  }

  function showGraphMessage(title, copy) {
    dom.graphMessageTitle.textContent = title;
    dom.graphMessageCopy.textContent = copy;
    dom.graphMessage.hidden = false;
  }

  function hideGraphMessage() {
    dom.graphMessage.hidden = true;
  }

  function issueText(value) {
    if (typeof value === "string") return compactText(value, "", 260);
    const issue = asObject(value);
    return compactText(issue.message || issue.code || issue.title, "State issue", 260);
  }

  function renderHealth() {
    const { health } = ui.payload;
    const issueCount = health.issues.length;
    const warningCount = health.warnings.length;
    const normalizedStatus = health.status;
    let healthMode = "healthy";
    if (["error", "failed", "unhealthy"].includes(normalizedStatus) || issueCount > 0) {
      healthMode = "error";
    } else if (["degraded", "warning", "warnings"].includes(normalizedStatus) || warningCount > 0) {
      healthMode = "degraded";
    }
    dom.footerHealth.dataset.health = healthMode;

    if (healthMode === "healthy") {
      dom.footerHealthLabel.textContent = "State verified from local files";
      if (ui.payload.nodes.length === 0) {
        renderSurfaceState("empty", "No route nodes yet", "Run Wayfinder to formulate the first decisions and gates for this effort.");
      } else {
        renderSurfaceState("ready", "", "");
      }
      return;
    }

    const details = [...health.issues, ...health.warnings].map(issueText).filter(Boolean);
    const summary = details.slice(0, 2).join(" · ");
    if (healthMode === "error") {
      dom.footerHealthLabel.textContent = `${issueCount} state issue${issueCount === 1 ? "" : "s"}`;
      renderSurfaceState("degraded", "State needs attention", summary || "The local state contains validation errors. Counts and relationships may be incomplete.");
    } else {
      dom.footerHealthLabel.textContent = `${warningCount} state warning${warningCount === 1 ? "" : "s"}`;
      renderSurfaceState("degraded", "State loaded with warnings", summary || "Some map details may be incomplete. Run Wayfinder doctor for the recorded warnings.");
    }
  }

  function clearPayloadSurfaces() {
    ui.selectedId = null;
    ui.selectedPhaseId = null;
    ui.visibleNodeIds = [];
    dom.projectState.textContent = "Unavailable";
    dom.phaseList.replaceChildren();
    dom.checkpointSummary.hidden = true;
    dom.graphEdges.replaceChildren();
    dom.graphNodes.replaceChildren();
    dom.listBody.replaceChildren();
    dom.listMessage.textContent = "No local decision state is available.";
    dom.listMessage.hidden = false;
    dom.activityList.replaceChildren();
    dom.activityEmpty.textContent = "Activity is unavailable until local state reconnects.";
    dom.activityEmpty.hidden = false;
    dom.viewAllActivity.hidden = true;
    dom.statusCounts.replaceChildren();
    dom.overviewMetrics.replaceChildren();
    dom.overviewHealthList.replaceChildren();
    dom.secondaryWorkstreams.replaceChildren();
    dom.comparisonList.replaceChildren();
    dom.baselineList.replaceChildren();
    dom.evidenceList.replaceChildren();
    dom.assumptionsList.replaceChildren();
    dom.invariantsList.replaceChildren();
    dom.checkpointCards.replaceChildren();
    dom.milestoneList.replaceChildren();
    dom.recordingForm.hidden = true;
    dom.recordingFeedback.textContent = "";
    dom.inspectorContent.hidden = true;
    dom.inspectorEmpty.hidden = false;
    dom.inspectorEmptyTitle.textContent = "Project state unavailable";
    dom.inspectorEmptyCopy.textContent = "Reconnect to local state before inspecting a decision.";
    setInspectorOpen(false);
    dom.footerHealth.dataset.health = "error";
    dom.footerHealthLabel.textContent = "Local state unavailable";
    dom.totalCount.textContent = "Route totals —";
  }

  async function loadSession() {
    try {
      const response = await fetch(SESSION_ENDPOINT, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Session unavailable");
      const rawSession = asObject(await response.json());
      const mode = rawSession.mode === "decision-recording" ? "decision-recording" : "read-only";
      ui.session = {
        mode,
        csrfToken: mode === "decision-recording" ? asText(rawSession.csrf_token) : "",
        recordable: mode === "decision-recording" && asBoolean(rawSession.recordable_current_question),
      };
    } catch (_error) {
      ui.session = { mode: "read-only", csrfToken: "", recordable: false };
    }
    renderInteractionMode();
  }

  async function loadState() {
    renderSurfaceState("loading", "Loading project state", "Reading the active Wayfinder effort from local files.");
    dom.refresh.disabled = true;
    dom.refresh.setAttribute("aria-busy", "true");
    try {
      const [response] = await Promise.all([
        fetch(API_ENDPOINT, { headers: { Accept: "application/json" }, cache: "no-store" }),
        loadSession(),
      ]);
      if (!response.ok) {
        throw new Error(`State request returned ${response.status}`);
      }
      const payload = normalizePayload(await response.json());
      ui.payload = payload;
      ui.selectedPhaseId = payload.currentPhase || payload.phases.find((phase) => phase.state === "current")?.id || payload.phases[0]?.id;
      if (ui.selectedId && !payload.nodes.some((node) => node.id === ui.selectedId)) {
        ui.selectedId = null;
      }
      if (!ui.selectedId) ui.selectedId = chooseNeedsYouNode(payload.nodes)?.id || null;
      const route = parseRoute();
      if (route.nodeId && payload.nodes.some((node) => node.id === route.nodeId)) {
        ui.selectedId = route.nodeId;
      }
      ui.graphHasFramed = false;
      renderAll();
      renderHealth();
      applyRoute(false);
    } catch (error) {
      ui.payload = null;
      const message = error instanceof Error ? error.message : "The local state could not be read.";
      clearPayloadSurfaces();
      renderSurfaceState("error", "Could not load this Wayfinder map", `${message}. Check that the local dashboard server is still running, then try again.`);
      showGraphMessage("Map unavailable", "The dashboard has not received a valid local state payload.");
    } finally {
      dom.refresh.disabled = false;
      dom.refresh.removeAttribute("aria-busy");
    }
  }

  function chooseNeedsYouNode(nodes) {
    const humanTokens = ["hitl", "hybrid", "human", "owner", "user"];
    const decisions = nodes.filter((node) => node.kind !== "gate");
    const actionable = decisions.filter((node) => nodeStatusGroup(node) === "actionable");
    return actionable.find((node) => {
      const ownership = `${node.autonomy} ${node.nextActor}`.toLowerCase();
      return humanTokens.some((token) => ownership.includes(token));
    }) || actionable[0] || decisions.find((node) => nodeStatusGroup(node) === "claimed") || decisions[0] || nodes[0];
  }

  function renderAll() {
    renderProject();
    renderPhases();
    renderFooter();
    renderActivity();
    renderFilteredSurfaces();
    renderInspector();
    renderOverview();
    renderEvidence();
    renderAssumptions();
    renderInvariants();
    renderCheckpoints();
    renderInteractionMode();
  }

  function renderProject() {
    const project = ui.payload.project;
    dom.projectTitle.textContent = project.title;
    updateDocumentTitle();
    dom.projectState.textContent = projectStatusLabel(project.status);
    const contextLabels = document.querySelectorAll(".context-label");
    if (contextLabels[0]) contextLabels[0].hidden = !project.local;
  }

  function phaseVisualState(phase, index) {
    const explicit = normalizedToken(phase.state);
    if (["complete", "completed", "resolved"].includes(explicit)) return "complete";
    if (["current", "active", "in-progress", "in_progress"].includes(explicit)) return "current";
    if (["due", "recommended", "revalidate"].includes(explicit)) return "current";
    const currentIndex = ui.payload.phases.findIndex((item) => item.id === ui.payload.currentPhase);
    if (currentIndex >= 0) {
      if (index < currentIndex) return "complete";
      if (index === currentIndex) return "current";
    }
    return "upcoming";
  }

  function renderPhases() {
    const fragment = document.createDocumentFragment();
    ui.payload.phases.forEach((phase, index) => {
      const item = createElement("li", "phase-step");
      const state = phaseVisualState(phase, index);
      const selectedIsCurrent = phase.id === ui.payload.currentPhase;
      const runRecommendedNow = selectedIsCurrent && ui.payload.runRecommendation.recommended;
      const recommended = phase.checkpoint.recommendedRun || runRecommendedNow;
      let runLabel = "";
      if (runRecommendedNow) {
        runLabel = "Run now";
      } else if (phase.checkpoint.recommendedRun && state === "complete") {
        runLabel = "Rerun if changed";
      } else if (phase.checkpoint.recommendedRun) {
        runLabel = "Run at checkpoint";
      }
      const phaseStateLabel = { complete: "Complete", current: "Current", upcoming: "Upcoming" }[state] || statusLabel(state);
      item.dataset.state = state;
      item.dataset.recommended = String(recommended);

      const button = createElement("button");
      button.type = "button";
      button.setAttribute("aria-label", `${phase.label}. ${phaseStateLabel}. ${phase.checkpoint.label}${runLabel ? `. ${runLabel}` : ""}`);
      button.setAttribute("aria-pressed", String(ui.selectedPhaseId === phase.id));
      button.dataset.phaseId = phase.id;
      button.dataset.runLabel = runLabel;
      const node = createElement("span", "phase-node", state === "complete" ? "" : String(index + 1));
      node.setAttribute("aria-hidden", "true");
      const label = createElement("span", "phase-label", phase.label);
      label.dataset.runLabel = runLabel;
      button.append(node, label);
      item.append(button);
      fragment.append(item);
    });
    dom.phaseList.replaceChildren(fragment);
    const route = dom.phaseList.closest(".phase-route");
    const currentButton = dom.phaseList.querySelector(`button[data-phase-id="${CSS.escape(ui.payload.currentPhase)}"]`);
    const currentItem = currentButton?.closest(".phase-step");
    if (route && currentItem && route.scrollWidth > route.clientWidth) {
      const routeBox = route.getBoundingClientRect();
      const itemBox = currentItem.getBoundingClientRect();
      const itemCenter = itemBox.left - routeBox.left + route.scrollLeft + itemBox.width / 2;
      const centered = itemCenter - route.clientWidth / 2;
      route.scrollLeft = Math.max(0, Math.min(route.scrollWidth - route.clientWidth, centered));
    }
    renderCheckpointSummary();
  }

  function renderCheckpointSummary() {
    const phase = ui.payload.phases.find((item) => item.id === ui.selectedPhaseId);
    if (!phase) {
      dom.checkpointSummary.hidden = true;
      return;
    }
    dom.checkpointSummary.hidden = false;
    dom.checkpointLabel.textContent = phase.checkpoint.label;
    const selectedIsCurrent = phase.id === ui.payload.currentPhase;
    const runRecommendation = ui.payload.runRecommendation;
    if (phase.checkpoint.recommendedRun) {
      dom.checkpointRecommendation.textContent = `Run Wayfinder recommended — ${phase.checkpoint.reason}`;
    } else if (selectedIsCurrent && runRecommendation.level !== "none") {
      dom.checkpointRecommendation.textContent = `${runRecommendation.label} — ${runRecommendation.reason}`;
    } else {
      dom.checkpointRecommendation.textContent = phase.checkpoint.reason;
    }
  }

  function renderFooter() {
    const fragment = document.createDocumentFragment();
    const counts = ui.payload.counts;
    const labels = {
      resolved: "Resolved",
      actionable: "Actionable",
      waiting: "Waiting",
      blocked: "Blocked",
      claimed: "Claimed",
      gates: "Gate",
    };
    STATUS_ORDER.forEach((status) => {
      const item = createElement("li", "footer-count");
      item.dataset.status = status;
      const icon = createElement("span", "count-mark");
      appendStatusIcon(icon, status === "gates" ? "waiting" : status, status === "gates" ? "gate" : "decision");
      const label = createElement("span", "count-label", labels[status]);
      const count = createElement("strong", "", String(counts[status]));
      item.append(icon, label, count);
      fragment.append(item);
    });
    dom.statusCounts.replaceChildren(fragment);
    dom.totalCount.textContent = `${counts.decisions} decisions · ${counts.gates} gates`;
  }

  function renderInteractionMode() {
    const interactive = ui.session.mode === "decision-recording" && Boolean(ui.session.csrfToken);
    dom.interactionMode.dataset.mode = interactive ? "interactive" : "read-only";
    dom.interactionModeLabel.textContent = interactive ? "Interactive local" : "Read-only";
    dom.recordingModeBadge.textContent = interactive ? "Interactive" : "Read-only";
    dom.recordingModeBadge.dataset.mode = interactive ? "interactive" : "read-only";
    if (!interactive) {
      dom.recordingModeNote.textContent = "This launch is read-only. Restart with --interactive to record the current bounded intake answer.";
    }
  }

  function renderOverview() {
    const payload = ui.payload;
    const phase = payload.phases.find((item) => item.id === payload.currentPhase) || payload.phases[0];
    dom.overviewDestination.textContent = payload.project.destination;
    dom.overviewUpdated.textContent = payload.project.lastUpdated
      ? `Canonical state updated ${formatActivityTime(payload.project.lastUpdated)}`
      : "Canonical update time is not recorded.";
    dom.overviewRecommendationCard.dataset.level = payload.runRecommendation.level;
    dom.overviewRecommendation.textContent = payload.runRecommendation.label;
    dom.overviewRecommendationReason.textContent = payload.runRecommendation.reason;
    const needsYou = chooseNeedsYouNode(payload.nodes);
    dom.overviewNextLink.href = needsYou ? `#/decisions/${encodeURIComponent(needsYou.id)}` : "#/decisions";
    dom.overviewNextLink.textContent = needsYou ? `Review ${needsYou.id}` : "Review decisions";

    const metrics = [
      ["Actionable", payload.counts.actionable, "actionable"],
      ["Waiting", payload.counts.waiting, "waiting"],
      ["Resolved", payload.counts.resolved, "resolved"],
      ["Delivery gates", payload.counts.gates, "gates"],
    ];
    const metricFragment = document.createDocumentFragment();
    metrics.forEach(([label, value, status]) => {
      const card = createElement("article", "metric-card");
      card.dataset.status = status;
      const mark = createElement("span", "metric-mark");
      appendStatusIcon(mark, status === "gates" ? "waiting" : status, status === "gates" ? "gate" : "decision");
      card.append(mark, createElement("strong", "", String(value)), createElement("span", "", label));
      metricFragment.append(card);
    });
    dom.overviewMetrics.replaceChildren(metricFragment);

    dom.overviewPhase.textContent = phase?.label || "Not determined";
    dom.overviewPhaseDescription.textContent = phase?.description || "No phase description is recorded.";
    const phaseFragment = document.createDocumentFragment();
    payload.phases.forEach((item, index) => {
      const segment = createElement("span", "mini-progress-segment");
      segment.dataset.state = phaseVisualState(item, index);
      segment.title = `${item.label}: ${statusLabel(segment.dataset.state)}`;
      phaseFragment.append(segment);
    });
    dom.overviewPhaseProgress.replaceChildren(phaseFragment);

    const issueCount = payload.health.issues.length;
    const warningCount = payload.health.warnings.length;
    const healthy = issueCount === 0 && warningCount === 0;
    dom.overviewHealth.textContent = healthy ? "Canonical route is internally consistent" : issueCount ? "Route integrity needs attention" : "Route loaded with warnings";
    dom.overviewHealthDetail.textContent = healthy
      ? "State was derived from bounded, validated local artifacts."
      : `${issueCount} error${issueCount === 1 ? "" : "s"} and ${warningCount} warning${warningCount === 1 ? "" : "s"} are visible without hiding route nodes.`;
    const healthFragment = document.createDocumentFragment();
    [...payload.health.issues, ...payload.health.warnings].slice(0, 3).forEach((issue) => {
      healthFragment.append(createElement("li", "", issueText(issue)));
    });
    if (healthy) {
      healthFragment.append(createElement("li", "", `${payload.nodes.length} route nodes passed the public state boundary.`));
      healthFragment.append(createElement("li", "", `${payload.edges.length} typed relationships are available to the map.`));
    }
    dom.overviewHealthList.replaceChildren(healthFragment);
    renderBriefing();
  }

  function domainLabel(value) {
    const labels = {
      SOFTWARE: "Software delivery",
      GENERAL_PROJECT: "General project delivery",
      FINANCE_REPORTING: "Finance and reporting",
      OTHER: "Other execution route",
    };
    return labels[asText(value).toUpperCase()] || "Primary workstream not confirmed";
  }

  function renderBriefing() {
    const intake = ui.payload.intake;
    const baseline = ui.payload.implementationBaseline;
    const progress = intake.progress;
    dom.intakeProgress.textContent = progress.total === null
      ? "Intake not started"
      : `${progress.answered} of ${progress.total} answered · ${progress.percent}%`;
    dom.primaryWorkstream.textContent = intake.domain.primary
      ? domainLabel(intake.domain.primary)
      : intake.domain.proposed
        ? `${domainLabel(intake.domain.proposed)} proposed · awaiting confirmation`
        : "Primary workstream not confirmed";

    const workstreamFragment = document.createDocumentFragment();
    intake.domain.secondaryWorkstreams.forEach((workstream) => {
      const item = createElement("article", "workstream-item");
      const heading = createElement("div");
      heading.append(createElement("span", "record-id", workstream.id), createElement("strong", "", domainLabel(workstream.domain)));
      item.append(heading, createElement("p", "", workstream.outcome));
      const meta = createElement("small", "", `${workstream.authority}${workstream.decisions.length ? ` · ${workstream.decisions.join(", ")}` : ""}`);
      item.append(meta);
      workstreamFragment.append(item);
    });
    dom.secondaryWorkstreams.replaceChildren(workstreamFragment);
    dom.secondaryWorkstreamsEmpty.hidden = intake.domain.secondaryWorkstreams.length > 0;

    const comparisons = intake.comparisons;
    const hasNamedTechnology = comparisons.some((comparison) => comparison.level === "named-technology");
    const hasStrategy = comparisons.some((comparison) => comparison.level === "strategy");
    dom.comparisonLevel.textContent = hasNamedTechnology && hasStrategy
      ? "Strategy + named technology"
      : hasNamedTechnology
        ? "Named technology"
        : "Strategy";
    dom.comparisonTitle.textContent = comparisons.length
      ? `${comparisons.length} route comparison${comparisons.length === 1 ? "" : "s"}`
      : "No route comparison recorded";
    dom.comparisonExplanation.textContent = hasNamedTechnology
      ? "Architecture or operating strategy and named technologies are shown as separate comparison levels. Expand an option for exact tradeoffs and sources."
      : "Architecture or operating-strategy comparisons appear here. They do not claim to compare named technologies or vendors.";
    const comparisonFragment = document.createDocumentFragment();
    comparisons.forEach((comparison) => {
      const block = createElement("section", "comparison-block");
      const heading = createElement("header", "comparison-block-heading");
      heading.append(
        createElement("strong", "", comparison.title),
        createElement("span", "", comparison.level === "named-technology" ? "Named technology" : "Route strategy"),
      );
      block.append(heading);
      if (comparison.criteria.length) {
        const criteria = createElement("p", "comparison-criteria", `Criteria: ${comparison.criteria.join(" · ")}`);
        block.append(criteria);
      }
      if (comparison.rationale) block.append(createElement("p", "comparison-rationale", comparison.rationale));
      const options = createElement("div", "comparison-options");
      comparison.options.forEach((option) => {
        const row = createElement("article", "comparison-row");
        const selected = option.id === comparison.selectedOption;
        const recommended = option.id === comparison.recommendedOption || option.recommendation;
        row.dataset.recommended = String(recommended);
        row.dataset.selected = String(selected);
        const label = createElement("strong", "", `${option.id} · ${option.label}`);
        const state = selected ? "Selected" : recommended ? "Recommended" : "Option";
        row.append(label, createElement("span", "", state));
        if (option.summary) row.append(createElement("p", "", option.summary));
        if (option.tradeoffs.length || option.evidenceRefs.length) {
          const details = createElement("details", "comparison-details");
          details.append(createElement("summary", "", "Review tradeoffs and evidence"));
          if (option.tradeoffs.length) {
            const tradeoffs = createElement("dl", "comparison-tradeoffs");
            option.tradeoffs.forEach((tradeoff) => tradeoffs.append(labeledDetail(tradeoff.label, tradeoff.value)));
            details.append(tradeoffs);
          }
          if (option.evidenceRefs.length) {
            const sources = createElement("div", "comparison-sources");
            sources.append(createElement("strong", "", "Sources and evidence"));
            option.evidenceRefs.forEach((reference) => sources.append(createSourceReference(reference)));
            details.append(sources);
          }
          row.append(details);
        }
        options.append(row);
      });
      if (!comparison.options.length) options.append(createElement("p", "empty-inline", "No bounded options are recorded for this comparison."));
      block.append(options);
      comparisonFragment.append(block);
    });
    dom.comparisonList.replaceChildren(comparisonFragment);

    dom.baselineEffort.textContent = baseline.effortId || "No effort ID";
    dom.baselineRevision.textContent = baseline.destinationRevision === null
      ? `Intake revision ${baseline.intakeRevision}`
      : `Destination revision ${baseline.destinationRevision} · intake ${baseline.intakeRevision}`;
    dom.baselineHandoff.textContent = ui.payload.exit.executionHandoff;
    const baselineFragment = document.createDocumentFragment();
    baseline.applicableDecisions.slice(0, 8).forEach((decision) => {
      baselineFragment.append(labeledDetail(
        decision.id,
        `revision ${decision.revision === null ? "not recorded" : decision.revision} · ${statusLabel(decision.status)}`,
      ));
    });
    if (!baseline.applicableDecisions.length) {
      baselineFragment.append(labeledDetail("Decisions", "No applicable decision revisions yet"));
    }
    baselineFragment.append(labeledDetail("Manifest hash", baseline.manifestHash || "Not available"));
    dom.baselineList.replaceChildren(baselineFragment);
  }

  function labeledDetail(term, value) {
    const row = createElement("div", "record-detail");
    row.append(createElement("dt", "", term), createElement("dd", "", value));
    return row;
  }

  function recordCard(id, title, status, summary) {
    const card = createElement("article", "record-card");
    card.dataset.status = normalizedToken(status);
    const header = createElement("header");
    header.append(createElement("span", "record-id", id), createElement("span", "record-status", statusLabel(status)));
    card.append(header, createElement("h3", "", title), createElement("p", "", summary));
    return card;
  }

  function renderEvidence() {
    const payload = ui.payload;
    const evidenceFragment = document.createDocumentFragment();
    payload.evidence.forEach((item) => {
      const card = recordCard(item.id, item.title, item.freshness, item.conclusion);
      const details = createElement("dl", "record-details");
      details.append(
        labeledDetail("Method", item.method),
        labeledDetail("Observed", item.observedAt ? formatActivityTime(item.observedAt) : "Not recorded"),
        labeledDetail("Subject revision", item.subjectRevision),
        labeledDetail("Source", `${statusLabel(item.sourceType)} · ${item.source}`),
        labeledDetail("Collector", item.collector),
        labeledDetail("Basis", item.basis || "Not recorded"),
        labeledDetail("Confidence", statusLabel(item.confidence)),
        labeledDetail("Sensitivity", statusLabel(item.sensitivity)),
        labeledDetail("Revalidate", item.revalidateWhen),
      );
      card.append(details);
      evidenceFragment.append(card);
    });
    dom.evidenceList.replaceChildren(evidenceFragment);
    dom.evidenceEmpty.hidden = payload.evidence.length > 0;
    dom.evidenceCount.textContent = `${payload.evidence.length} record${payload.evidence.length === 1 ? "" : "s"}`;

    const exit = payload.exit;
    dom.exitStatus.textContent = exit.complete ? "Complete" : exit.planningExitReady ? "Ready for execution" : "Pending";
    dom.exitHeading.textContent = exit.complete
      ? "Route proof and EXIT receipt are complete"
      : exit.planningExitReady
        ? "Planning exit proof passes; the execution handoff receipt is still pending"
        : "Destination-blocking proof is still open";
    const categories = [
      ["Unresolved destination decisions", exit.unresolvedDestinationDecisions],
      ["Pending delivery gates", exit.pendingDeliveryGates],
      ["High-impact assumptions", exit.highImpactOpenAssumptions],
      ["Unformulated fog", exit.unformulatedFog],
      ["Deferred non-blocking unknowns", exit.remainingNonblockingUnknowns],
    ];
    const exitFragment = document.createDocumentFragment();
    categories.forEach(([label, values]) => {
      exitFragment.append(createElement("li", "", `${label}: ${values.length ? values.join(", ") : "none"}`));
    });
    dom.exitList.replaceChildren(exitFragment);
  }

  function renderAssumptions() {
    const fragment = document.createDocumentFragment();
    ui.payload.assumptions.forEach((item) => {
      const card = recordCard(item.id, item.summary, item.status, item.impact);
      const details = createElement("dl", "record-details");
      details.append(
        labeledDetail("Route impact", item.destinationBlocking ? "Destination-blocking" : "Non-blocking"),
        labeledDetail("Confidence", statusLabel(item.confidence)),
        labeledDetail("Affects", item.affects.join(", ") || "No linked nodes"),
        labeledDetail("Evidence", item.evidence.join(", ") || "No linked evidence"),
        labeledDetail("Revalidate", item.revalidateWhen),
      );
      card.append(details);
      fragment.append(card);
    });
    dom.assumptionsList.replaceChildren(fragment);
    dom.assumptionsEmpty.hidden = ui.payload.assumptions.length > 0;
  }

  function renderInvariants() {
    const fragment = document.createDocumentFragment();
    ui.payload.invariants.forEach((item) => {
      const card = recordCard(item.id, item.invariant, item.status, item.rationale);
      const details = createElement("dl", "record-details");
      details.append(
        labeledDetail("Scope", item.scope),
        labeledDetail("Enforcement", item.enforcement),
        labeledDetail("Responsible", item.responsibleParty),
        labeledDetail("Evidence", item.evidence.join(", ") || "No linked evidence"),
        labeledDetail("Revalidate", item.revalidateWhen),
      );
      card.append(details);
      fragment.append(card);
    });
    dom.invariantsList.replaceChildren(fragment);
    dom.invariantsEmpty.hidden = ui.payload.invariants.length > 0;
  }

  function renderCheckpoints() {
    const payload = ui.payload;
    dom.checkpointCallout.dataset.level = payload.runRecommendation.level;
    dom.checkpointCalloutLabel.textContent = payload.runRecommendation.label;
    dom.checkpointCalloutReason.textContent = payload.runRecommendation.reason;
    const checkpointFragment = document.createDocumentFragment();
    payload.phases.forEach((phase, index) => {
      const item = createElement("li", "checkpoint-card");
      const visualState = phaseVisualState(phase, index);
      item.dataset.state = visualState;
      item.dataset.recommended = String(phase.checkpoint.recommendedRun || (phase.id === payload.currentPhase && payload.runRecommendation.recommended));
      const number = createElement("span", "checkpoint-number", String(index + 1));
      const body = createElement("div");
      const heading = createElement("div", "checkpoint-card-heading");
      heading.append(createElement("h3", "", phase.label), createElement("span", "checkpoint-state", visualState === "complete" ? "Complete" : visualState === "current" ? "Current" : "Upcoming"));
      body.append(heading, createElement("p", "", phase.description));
      const trigger = createElement("div", "checkpoint-trigger");
      trigger.append(createElement("strong", "", phase.checkpoint.label), createElement("span", "", phase.checkpoint.reason));
      if (phase.checkpoint.recommendedRun) trigger.append(createElement("em", "", visualState === "complete" ? "Rerun if changed" : "Run at checkpoint"));
      body.append(trigger);
      item.append(number, body);
      checkpointFragment.append(item);
    });
    dom.checkpointCards.replaceChildren(checkpointFragment);

    const milestoneFragment = document.createDocumentFragment();
    let reached = 0;
    payload.milestones.forEach((milestone) => {
      const row = createElement("div", "milestone-row");
      const complete = ["complete", "completed", "reached", "resolved"].includes(milestone.state);
      if (complete) reached += 1;
      row.dataset.state = complete ? "complete" : milestone.state;
      const mark = createElement("span", "milestone-mark");
      if (complete) appendStatusIcon(mark, "resolved");
      row.append(mark);
      const copy = createElement("div");
      copy.append(createElement("strong", "", `${milestone.id} · ${milestone.label}`), createElement("p", "", milestone.criteria));
      row.append(copy, createElement("span", "milestone-state", complete ? "Reached" : statusLabel(milestone.state)));
      milestoneFragment.append(row);
    });
    dom.milestoneList.replaceChildren(milestoneFragment);
    dom.milestoneEmpty.hidden = payload.milestones.length > 0;
    dom.milestoneProgress.textContent = `${reached} of ${payload.milestones.length || 5} reached`;
  }

  function nodeMatchesFilters(node) {
    const query = ui.query.trim().toLowerCase();
    const searchHaystack = `${node.id} ${node.title} ${node.question} ${node.summary} ${node.responsibleParty} ${node.nextActor}`.toLowerCase();
    const queryMatches = !query || searchHaystack.includes(query);
    const statusMatches = ui.status === "all" || nodeStatusGroup(node) === ui.status;
    const kindMatches = ui.kind === "all" || node.kind === ui.kind || (ui.kind === "checkpoint" && node.kind.includes("checkpoint"));
    return queryMatches && statusMatches && kindMatches;
  }

  function filteredNodes() {
    return ui.payload ? ui.payload.nodes.filter(nodeMatchesFilters) : [];
  }

  function renderFilteredSurfaces() {
    const nodes = filteredNodes();
    ui.visibleNodeIds = nodes.map((node) => node.id);
    renderGraph(nodes);
    renderList(nodes);
  }

  function displayEdge(edge) {
    if (edge.type === "requires" || edge.type === "informs") {
      return { from: edge.target, to: edge.source, type: edge.type };
    }
    return { from: edge.source, to: edge.target, type: edge.type };
  }

  function buildLayout(nodes, edges) {
    const nodeIds = new Set(nodes.map((node) => node.id));
    const displayEdges = edges.map(displayEdge).filter((edge) => nodeIds.has(edge.from) && nodeIds.has(edge.to));
    const incoming = new Map(nodes.map((node) => [node.id, 0]));
    const outgoing = new Map(nodes.map((node) => [node.id, []]));
    displayEdges.forEach((edge) => {
      incoming.set(edge.to, (incoming.get(edge.to) || 0) + 1);
      outgoing.get(edge.from)?.push(edge.to);
    });

    const rank = new Map(nodes.map((node) => [node.id, 0]));
    const queue = nodes.filter((node) => incoming.get(node.id) === 0).map((node) => node.id).sort();
    const visited = new Set();
    while (queue.length) {
      const id = queue.shift();
      if (!id || visited.has(id)) continue;
      visited.add(id);
      (outgoing.get(id) || []).forEach((target) => {
        rank.set(target, Math.max(rank.get(target) || 0, (rank.get(id) || 0) + 1));
        incoming.set(target, (incoming.get(target) || 1) - 1);
        if (incoming.get(target) === 0) queue.push(target);
      });
      queue.sort();
    }

    const phaseIndex = new Map(ui.payload.phases.map((phase, index) => [phase.id, index]));
    nodes.filter((node) => !visited.has(node.id)).forEach((node, index) => {
      rank.set(node.id, phaseIndex.has(node.phase) ? phaseIndex.get(node.phase) : index % 5);
    });

    const columns = new Map();
    nodes.forEach((node) => {
      const level = rank.get(node.id) || 0;
      if (!columns.has(level)) columns.set(level, []);
      columns.get(level).push(node);
    });
    const orderedLevels = [...columns.keys()].sort((a, b) => a - b);
    const maxRows = Math.max(1, ...[...columns.values()].map((items) => items.length));
    const width = Math.max(720, LAYOUT_PADDING_X * 2 + orderedLevels.length * NODE_WIDTH + Math.max(0, orderedLevels.length - 1) * COLUMN_GAP);
    const height = Math.max(430, LAYOUT_PADDING_Y * 2 + maxRows * NODE_HEIGHT + Math.max(0, maxRows - 1) * ROW_GAP);
    const positions = new Map();

    orderedLevels.forEach((level, columnIndex) => {
      const items = columns.get(level).sort((left, right) => left.id.localeCompare(right.id));
      const columnHeight = items.length * NODE_HEIGHT + Math.max(0, items.length - 1) * ROW_GAP;
      const startY = Math.max(LAYOUT_PADDING_Y, (height - columnHeight) / 2);
      items.forEach((node, rowIndex) => {
        positions.set(node.id, {
          x: LAYOUT_PADDING_X + columnIndex * (NODE_WIDTH + COLUMN_GAP),
          y: startY + rowIndex * (NODE_HEIGHT + ROW_GAP),
        });
      });
    });

    return { positions, width, height, displayEdges };
  }

  function edgePath(from, to) {
    const sourceCenterY = from.y + NODE_HEIGHT / 2;
    const targetCenterY = to.y + NODE_HEIGHT / 2;
    if (to.x > from.x + NODE_WIDTH * 0.45) {
      const startX = from.x + NODE_WIDTH;
      const endX = to.x;
      const controlDistance = Math.max(34, (endX - startX) * 0.48);
      return `M${startX},${sourceCenterY} C${startX + controlDistance},${sourceCenterY} ${endX - controlDistance},${targetCenterY} ${endX},${targetCenterY}`;
    }
    const startX = from.x + NODE_WIDTH / 2;
    const startY = from.y + NODE_HEIGHT;
    const endX = to.x + NODE_WIDTH / 2;
    const endY = to.y;
    const bendY = Math.max(startY, endY) + 34;
    return `M${startX},${startY} C${startX},${bendY} ${endX},${bendY} ${endX},${endY}`;
  }

  function wrapTitle(title, maxCharacters = 23) {
    const words = asText(title).split(/\s+/).filter(Boolean);
    const lines = [];
    let current = "";
    words.forEach((word) => {
      const candidate = current ? `${current} ${word}` : word;
      if (candidate.length <= maxCharacters || current === "") {
        current = candidate;
      } else {
        lines.push(current);
        current = word;
      }
    });
    if (current) lines.push(current);
    if (lines.length > 2) {
      lines[1] = `${lines.slice(1).join(" ").slice(0, maxCharacters - 1)}…`;
      return lines.slice(0, 2);
    }
    return lines.length ? lines : ["Untitled decision"];
  }

  function renderGraphNode(node, position) {
    const group = createSvgElement("g", {
      class: "graph-node",
      transform: `translate(${position.x} ${position.y})`,
      tabindex: "0",
      role: "button",
      "aria-label": `${node.id}, ${node.title}, ${node.kind}, ${nodeStatusLabel(node)}`,
      "data-status": nodeStatusGroup(node),
      "data-kind": node.kind,
      "data-selected": String(node.id === ui.selectedId),
    });
    group.dataset.nodeId = node.id;
    const title = createSvgElement("title");
    title.textContent = `${node.id}: ${node.title} — ${nodeStatusLabel(node)}`;
    group.append(title);

    if (node.kind === "gate") {
      group.append(createSvgElement("polygon", {
        class: "node-shape",
        points: `18,0 ${NODE_WIDTH - 18},0 ${NODE_WIDTH},${NODE_HEIGHT / 2} ${NODE_WIDTH - 18},${NODE_HEIGHT} 18,${NODE_HEIGHT} 0,${NODE_HEIGHT / 2}`,
      }));
    } else {
      group.append(createSvgElement("rect", {
        class: "node-shape",
        x: 0,
        y: 0,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        rx: 7,
      }));
    }

    const idBox = createSvgElement("rect", { class: "node-id-box", x: 12, y: 12, width: 26, height: 22, rx: 4 });
    const idText = createSvgElement("text", { class: "node-id-text", x: 25, y: 27, "text-anchor": "middle" });
    idText.textContent = node.id.replace(/^D-0*/, "").replace(/^G-0*/, "G");
    group.append(idBox, idText);

    const titleText = createSvgElement("text", { class: "node-title", x: 47, y: 20 });
    wrapTitle(node.title).forEach((line, index) => {
      const tspan = createSvgElement("tspan", { x: 47, dy: index === 0 ? 0 : 15 });
      tspan.textContent = line;
      titleText.append(tspan);
    });
    group.append(titleText);

    const statusIcon = appendStatusIcon(group, node.kind === "gate" ? node.status : nodeStatusGroup(node), node.kind, true);
    statusIcon.setAttribute("transform", "translate(22 61)");
    const meta = createSvgElement("text", { class: "node-meta", x: 34, y: 64 });
    meta.textContent = node.kind === "gate" ? `Gate · ${nodeStatusLabel(node)}` : nodeStatusLabel(node);
    group.append(meta);

    group.addEventListener("click", () => selectNode(node.id, true));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(node.id, true);
      } else if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        moveGraphFocus(node.id, event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1);
      }
    });
    return group;
  }

  function renderGraph(nodes) {
    dom.graphEdges.replaceChildren();
    dom.graphNodes.replaceChildren();
    if (!nodes.length) {
      ui.graphPositions = new Map();
      ui.graphWidth = 1000;
      ui.graphHeight = 600;
      ui.zoom = 1;
      ui.offsetX = 0;
      ui.offsetY = 0;
      ui.graphMode = "empty";
      dom.graph.setAttribute("viewBox", "0 0 1000 600");
      dom.graphModeLabel.textContent = ui.payload.nodes.length ? "No visible route nodes" : "No route nodes yet";
      applyGraphTransform();
      showGraphMessage(
        ui.payload.nodes.length ? "No route nodes match these filters" : "No route nodes yet",
        ui.payload.nodes.length ? "Adjust the search, status, or kind filter to restore nodes." : "Run Wayfinder to formulate the first decision route for this effort."
      );
      return;
    }
    hideGraphMessage();
    const layout = buildLayout(nodes, ui.payload.edges);
    ui.graphWidth = layout.width;
    ui.graphHeight = layout.height;
    ui.graphPositions = layout.positions;
    dom.graph.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);

    const edgeFragment = document.createDocumentFragment();
    layout.displayEdges.forEach((edge) => {
      const from = layout.positions.get(edge.from);
      const to = layout.positions.get(edge.to);
      if (!from || !to) return;
      const path = createSvgElement("path", {
        class: "edge",
        d: edgePath(from, to),
        "data-edge-type": edge.type,
        "data-muted": String(Boolean(ui.selectedId && edge.from !== ui.selectedId && edge.to !== ui.selectedId)),
      });
      const title = createSvgElement("title");
      title.textContent = `${edge.from} ${edge.type} ${edge.to}`;
      path.append(title);
      edgeFragment.append(path);
    });
    dom.graphEdges.append(edgeFragment);

    const nodeFragment = document.createDocumentFragment();
    nodes.forEach((node) => nodeFragment.append(renderGraphNode(node, layout.positions.get(node.id))));
    dom.graphNodes.append(nodeFragment);
    if (!ui.graphHasFramed || ui.graphMode === "empty" || (ui.graphMode === "focused" && !layout.positions.has(ui.selectedId))) {
      ui.graphHasFramed = true;
      if (nodes.length > 5 && layout.positions.has(ui.selectedId)) {
        focusSelectedGraphNode(false);
      } else {
        fitGraph(false);
      }
    } else {
      applyGraphTransform();
    }
  }

  function renderList(nodes) {
    const fragment = document.createDocumentFragment();
    nodes.forEach((node) => {
      const row = createElement("tr");
      row.dataset.nodeId = node.id;
      row.dataset.selected = String(node.id === ui.selectedId);
      const idCell = createElement("td", "table-node-id", node.id);
      const titleCell = createElement("td");
      const titleButton = createElement("button", "", node.title);
      titleButton.type = "button";
      titleButton.addEventListener("click", () => selectNode(node.id, true));
      titleCell.append(titleButton);
      const kindCell = createElement("td", "", node.kind === "gate" ? "Gate" : "Decision");
      const statusCell = createElement("td");
      const status = createElement("span", "table-status");
      status.dataset.status = nodeStatusGroup(node);
      appendStatusIcon(status, node.kind === "gate" ? node.status : nodeStatusGroup(node), node.kind);
      status.append(createElement("span", "", nodeStatusLabel(node)));
      statusCell.append(status);
      const actorCell = createElement("td", "", node.nextActor);
      const phase = ui.payload.phases.find((item) => item.id === node.phase);
      const phaseCell = createElement("td", "", phase?.label || node.phase || "Not assigned");
      row.append(idCell, titleCell, kindCell, statusCell, actorCell, phaseCell);
      fragment.append(row);
    });
    dom.listBody.replaceChildren(fragment);
    dom.listMessage.hidden = nodes.length > 0;
    if (!nodes.length) {
      dom.listMessage.textContent = ui.payload.nodes.length
        ? "No decisions match the current search and filters."
        : "No decisions or gates have been recorded for this effort yet.";
    }
  }

  function moveGraphFocus(currentId, direction) {
    const nodeElements = [...dom.graphNodes.querySelectorAll(".graph-node")];
    const currentIndex = nodeElements.findIndex((element) => element.dataset.nodeId === currentId);
    if (currentIndex < 0 || nodeElements.length < 2) return;
    const nextIndex = (currentIndex + direction + nodeElements.length) % nodeElements.length;
    nodeElements[nextIndex].focus();
  }

  function applyGraphTransform() {
    dom.graphViewport.setAttribute("transform", `translate(${ui.offsetX} ${ui.offsetY}) scale(${ui.zoom})`);
    const hasVisibleNodes = ui.graphPositions.size > 0;
    dom.zoomIn.disabled = !hasVisibleNodes || ui.zoom >= MAX_ZOOM;
    dom.zoomOut.disabled = !hasVisibleNodes || ui.zoom <= MIN_ZOOM;
    dom.fitMap.disabled = !hasVisibleNodes;
    dom.focusMap.disabled = !hasVisibleNodes || !ui.selectedId || !ui.graphPositions.has(ui.selectedId);
  }

  function setZoom(nextZoom) {
    const bounded = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));
    const centerX = ui.graphWidth / 2;
    const centerY = ui.graphHeight / 2;
    const ratio = bounded / ui.zoom;
    ui.offsetX = centerX - (centerX - ui.offsetX) * ratio;
    ui.offsetY = centerY - (centerY - ui.offsetY) * ratio;
    ui.zoom = bounded;
    applyGraphTransform();
  }

  function fitGraph(announce = true) {
    if (!ui.graphPositions.size) return;
    ui.zoom = 1;
    ui.offsetX = 0;
    ui.offsetY = 0;
    ui.graphMode = "full";
    dom.graphModeLabel.textContent = "Full route overview";
    applyGraphTransform();
    if (announce) showToast("Showing the full decision route.");
  }

  function focusSelectedGraphNode(announce = true) {
    const position = ui.graphPositions.get(ui.selectedId);
    if (!position) {
      if (announce) showToast("Select a visible decision before focusing the map.");
      return;
    }
    const rect = dom.graph.getBoundingClientRect();
    const baseScale = Math.max(0.01, Math.min(rect.width / ui.graphWidth, rect.height / ui.graphHeight));
    const focusZoom = Math.min(MAX_ZOOM, Math.max(1.45, FOCUSED_NODE_HEIGHT / (NODE_HEIGHT * baseScale)));
    const centerX = position.x + NODE_WIDTH / 2;
    const centerY = position.y + NODE_HEIGHT / 2;
    ui.zoom = focusZoom;
    ui.offsetX = ui.graphWidth * 0.42 - centerX * focusZoom;
    ui.offsetY = ui.graphHeight * 0.5 - centerY * focusZoom;
    ui.graphMode = "focused";
    dom.graphModeLabel.textContent = `Focused route · ${ui.selectedId}`;
    applyGraphTransform();
    if (announce) showToast(`Focused the route around ${ui.selectedId}.`);
  }

  function selectNode(nodeId, revealInspector) {
    if (!ui.payload?.nodes.some((node) => node.id === nodeId)) return;
    if (revealInspector && document.activeElement instanceof HTMLElement) {
      ui.inspectorOpener = document.activeElement;
    }
    ui.inspectorDismissedFor = null;
    ui.selectedId = nodeId;
    renderFilteredSurfaces();
    renderInspector();
    if (revealInspector) {
      const target = `#/decisions/${encodeURIComponent(nodeId)}`;
      if (window.location.hash !== target) {
        window.location.hash = target;
      } else {
        applyRoute(true);
      }
    }
  }

  function deriveUnlocks(node) {
    if (node.unlocks.length) return node.unlocks;
    const dependentIds = ui.payload.edges.filter((edge) => edge.type === "requires" && edge.target === node.id).map((edge) => edge.source);
    return dependentIds.map((id) => ui.payload.nodes.find((item) => item.id === id)?.title || id).filter(Boolean);
  }

  function renderInspector() {
    const node = ui.payload?.nodes.find((item) => item.id === ui.selectedId);
    const question = ui.payload?.intake.currentQuestion || null;
    const textQuestion = !node && isTextQuestion(question);
    if (!node && !textQuestion) {
      dom.inspectorEmptyTitle.textContent = "No human decision selected";
      dom.inspectorEmptyCopy.textContent = "Select an actionable node to inspect the exact ask and what it unlocks.";
      dom.inspectorEmpty.hidden = false;
      dom.inspectorContent.hidden = true;
      setInspectorOpen(false);
      renderRecording(null);
      return;
    }
    dom.inspectorEmpty.hidden = true;
    dom.inspectorContent.hidden = false;
    if (!modalInspectorQuery.matches && ui.route === "decisions") {
      setInspectorOpen(ui.inspectorDismissedFor !== inspectorContextSignature());
    }
    dom.selectedNodeId.textContent = node?.id || question.id;
    dom.selectedNodeTitle.textContent = node?.title || "Destination framing";
    dom.selectedNodeStatus.replaceChildren();
    if (node) {
      appendStatusIcon(dom.selectedNodeStatus, node.kind === "gate" ? node.status : nodeStatusGroup(node), node.kind);
      dom.selectedNodeStatus.append(createElement("span", "", nodeStatusLabel(node)));
      dom.selectedNodeStatus.dataset.status = nodeStatusGroup(node);
      dom.selectedNodeStatus.dataset.kind = node.kind;
      dom.selectedNodeQuestion.textContent = node.question;
      dom.selectedNodeRecommendation.textContent = node.recommendation;
      dom.selectedNodeConsequence.textContent = node.consequence;
      const unlocks = deriveUnlocks(node);
      dom.selectedNodeUnlocks.textContent = unlocks.length ? unlocks.join(", ") : "No direct unlocks are recorded.";
    } else {
      appendStatusIcon(dom.selectedNodeStatus, "actionable", "decision");
      dom.selectedNodeStatus.append(createElement("span", "", "Framing input"));
      dom.selectedNodeStatus.dataset.status = "actionable";
      dom.selectedNodeStatus.dataset.kind = "decision";
      dom.selectedNodeQuestion.textContent = question.prompt;
      dom.selectedNodeRecommendation.textContent = "Answer the current bounded framing question to advance Wayfinder intake.";
      dom.selectedNodeConsequence.textContent = "The next route decision cannot be formulated until this framing input is recorded.";
      dom.selectedNodeUnlocks.textContent = "The next intake question and its canonical decision ticket.";
    }

    const health = ui.payload.health;
    const healthDetails = [...health.issues, ...health.warnings].map(issueText).filter(Boolean);
    if (healthDetails.length) {
      dom.inspectorProvenance.hidden = false;
      dom.inspectorProvenance.textContent = `State warning: ${healthDetails.slice(0, 2).join(" · ")}`;
    } else if (node?.evidence.length) {
      dom.inspectorProvenance.hidden = false;
      dom.inspectorProvenance.textContent = `${node.evidence.length} evidence reference${node.evidence.length === 1 ? "" : "s"} recorded in local state.`;
    } else {
      dom.inspectorProvenance.hidden = true;
      dom.inspectorProvenance.textContent = "";
    }
    // Clipboard output crosses the dashboard trust boundary. Copy only the
    // normalized stable identifier, never a manifest-controlled path.
    const stableReference = node?.id || question.id;
    dom.openDecision.dataset.reference = stableReference;
    dom.openDecision.title = `Copy ${stableReference}`;
    renderRecording(node || null);
  }

  function isChoiceQuestion(question) {
    return Boolean(question && question.options.length && ["choice", "single-choice", "single_choice", "select"].includes(question.answerType));
  }

  function isTextQuestion(question) {
    return Boolean(question && ["text", "fact"].includes(question.answerType));
  }

  function renderRecording(node) {
    const intake = ui.payload?.intake;
    const question = intake?.currentQuestion || null;
    const interactive = ui.session.mode === "decision-recording" && Boolean(ui.session.csrfToken);
    const choiceQuestion = isChoiceQuestion(question);
    const matchesNode = choiceQuestion && node && question.decisionId === node.id;
    const textQuestion = isTextQuestion(question);
    const available = Boolean(ui.session.recordable && question && intake.revision !== null && (textQuestion || matchesNode));
    dom.openIntake.hidden = !(interactive && ui.session.recordable && question);
    dom.recordingForm.hidden = !(interactive && available);
    dom.recordingFeedback.textContent = "";
    dom.recordingOptions.replaceChildren();
    dom.recordingOptionsGroup.hidden = true;
    dom.recordingAnswerControl.hidden = true;
    dom.recordingWhy.hidden = true;
    dom.recordingWhy.textContent = "";

    if (!question) {
      dom.recordingModeNote.textContent = "There is no current intake question to record. The canonical route remains available above.";
      return;
    }
    if (!interactive) {
      dom.recordingModeNote.textContent = "This launch is read-only. Restart with --interactive to record the current bounded intake answer.";
      return;
    }
    if (!ui.session.recordable) {
      dom.recordingModeNote.textContent = "Interactive mode is active, but the server reports no recordable current question. Refresh after intake state advances.";
      return;
    }
    if (choiceQuestion && !matchesNode) {
      dom.recordingModeNote.textContent = `The current intake choice belongs to ${question.decisionId}. Open that exact decision to record an answer.`;
      return;
    }
    if (!available) {
      dom.recordingModeNote.textContent = "The current intake question is not eligible for dashboard recording.";
      return;
    }

    dom.recordingModeNote.textContent = "Only this current question can be recorded. The server revalidates the option and revision before an atomic write.";
    dom.recordingQuestion.textContent = question.prompt;
    dom.recordingWhy.textContent = question.why;
    dom.recordingWhy.hidden = !question.why;
    dom.recordingRevision.value = String(intake.revision);
    if (dom.recordingForm.dataset.questionId !== question.id) {
      dom.recordingAnswer.value = "";
      dom.recordingForm.dataset.questionId = question.id;
    }
    if (choiceQuestion) {
      dom.recordingOptionsGroup.hidden = false;
      const fragment = document.createDocumentFragment();
      question.options.forEach((option) => {
        const label = createElement("label", "recording-option");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = "intake-choice";
        input.value = option.id;
        input.required = true;
        const copy = createElement("span");
        copy.append(createElement("strong", "", `${option.id} · ${option.label}`));
        if (option.description) copy.append(createElement("small", "", option.description));
        label.append(input, copy);
        fragment.append(label);
      });
      dom.recordingOptions.append(fragment);
      dom.recordAnswer.textContent = "Record selected option";
    } else {
      dom.recordingAnswerControl.hidden = false;
      dom.recordingAnswer.maxLength = question.maxLength;
      dom.recordingAnswer.required = true;
      dom.recordingAnswerHint.textContent = `Single-line answer · ${question.maxLength} character maximum`;
      dom.recordAnswer.textContent = "Record answer";
    }
  }

  function formatActivityTime(timestamp) {
    if (!timestamp) return "Time not recorded";
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return compactText(timestamp, "Time not recorded", 40);
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function renderActivity() {
    const allItems = ui.payload.activity.filter((item) => ui.activityFilter === "all" || item.type === ui.activityFilter.slice(0, -1));
    const items = ui.showAllActivity ? allItems : allItems.slice(0, 2);
    const fragment = document.createDocumentFragment();
    items.forEach((activity) => {
      const item = createElement("li", "activity-item");
      item.dataset.type = activity.type;
      const mark = createElement("span", "activity-mark");
      mark.setAttribute("aria-label", activity.type === "invalidation" ? "Invalidation" : "Update");
      const time = createElement("time", "activity-time", formatActivityTime(activity.timestamp));
      if (activity.timestamp) time.dateTime = activity.timestamp;
      const message = createElement("span", "activity-message", activity.message);
      if (activity.actor) message.title = `Recorded by ${activity.actor}`;
      const node = createElement("span", "activity-node", activity.nodeId || "Project");
      item.append(mark, time, message, node);
      fragment.append(item);
    });
    dom.activityList.replaceChildren(fragment);
    dom.activityEmpty.textContent = ui.activityFilter === "all" || ui.payload.activity.length === 0
      ? "No recorded activity in this effort yet."
      : `No ${ui.activityFilter} match this filter.`;
    dom.activityEmpty.hidden = allItems.length > 0;
    dom.viewAllActivity.hidden = allItems.length <= 2;
    dom.viewAllActivity.textContent = ui.showAllActivity ? "Show less" : "View all";
  }

  function parseRoute() {
    const match = /^#\/(overview|map|decisions|evidence|assumptions|invariants|checkpoints)(?:\/((?:D|G)-\d{3,}))?$/.exec(window.location.hash);
    if (!match) return { name: "overview", nodeId: null, valid: false };
    if (match[2] && match[1] !== "decisions") return { name: "overview", nodeId: null, valid: false };
    return { name: match[1], nodeId: match[2] || null, valid: true };
  }

  function updateDocumentTitle() {
    const projectTitle = ui.payload?.project.title || "Wayfinder";
    const label = ui.route.replace(/^./, (letter) => letter.toUpperCase());
    document.title = `${label} · ${projectTitle} — Wayfinder`;
  }

  function applyRoute(focusHeading = true) {
    let route = parseRoute();
    if (!route.valid) {
      window.history.replaceState(null, "", "#/overview");
      route = { name: "overview", nodeId: null, valid: true };
    }
    if (route.nodeId && ui.payload && !ui.payload.nodes.some((node) => node.id === route.nodeId)) {
      window.history.replaceState(null, "", "#/decisions");
      route.nodeId = null;
    }
    ui.route = route.name;
    ui.view = route.name === "decisions" ? "list" : "map";
    if (route.name !== "decisions" && dom.inspector.dataset.open === "true") {
      ui.inspectorOpener = null;
      setInspectorOpen(false);
    }
    if (route.name === "decisions") {
      ui.selectedId = route.nodeId;
      if (route.nodeId) ui.inspectorDismissedFor = null;
    }

    document.querySelectorAll("[data-view]").forEach((view) => {
      view.hidden = view.dataset.view !== route.name;
    });
    document.querySelectorAll(".rail-link[data-route]").forEach((link) => {
      const active = link.dataset.route === route.name;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const routeHasFilters = route.name === "map" || route.name === "decisions";
    dom.routeToolbar.hidden = !routeHasFilters;
    const mapSelected = route.name === "map";
    dom.mapButton.classList.toggle("is-selected", mapSelected);
    dom.listButton.classList.toggle("is-selected", route.name === "decisions");
    dom.mapButton.setAttribute("aria-pressed", String(mapSelected));
    dom.listButton.setAttribute("aria-pressed", String(route.name === "decisions"));
    if (ui.payload && route.name === "decisions") {
      renderInspector();
      if (route.nodeId && modalInspectorQuery.matches) setInspectorOpen(true, true);
    }
    if (ui.payload && route.name === "map") {
      window.requestAnimationFrame(() => {
        renderGraph(filteredNodes());
        applyGraphTransform();
      });
    }
    if (mobileNavigationQuery.matches) setMobileNavigation(false, false);
    updateDocumentTitle();
    const focusDecisionSearch = ui.pendingFocusTarget === "decision-search" && route.name === "decisions";
    const focusInspectorOpener = ui.pendingFocusTarget === "inspector-opener" && route.name === "decisions";
    if (focusDecisionSearch) {
      ui.pendingFocusTarget = null;
      window.requestAnimationFrame(() => {
        if (ui.route === "decisions") dom.search.focus({ preventScroll: false });
      });
    } else if (focusInspectorOpener) {
      ui.pendingFocusTarget = null;
      window.requestAnimationFrame(() => {
        if (ui.route !== "decisions") return;
        if (!restoreInspectorOpener()) {
          document.querySelector('#view-decisions h2[tabindex="-1"]')?.focus({ preventScroll: false });
        }
      });
    } else if (focusHeading) {
      const heading = document.querySelector(`[data-view="${CSS.escape(route.name)}"] h2[tabindex="-1"]`);
      heading?.focus({ preventScroll: false });
    }
  }

  function setView(view) {
    const route = view === "list" ? "decisions" : "map";
    window.location.hash = `#/${route}`;
  }

  function setStatusFilter(status, button) {
    ui.status = status;
    dom.statusFilters.querySelectorAll("button").forEach((item) => {
      const selected = item === button;
      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    renderFilteredSurfaces();
  }

  async function copyDecisionReference() {
    const reference = asText(dom.openDecision.dataset.reference);
    if (!reference) return;
    try {
      await navigator.clipboard.writeText(reference);
      showToast(`Copied stable Wayfinder ID: ${reference}`);
    } catch (_error) {
      const selection = window.getSelection();
      const range = document.createRange();
      dom.selectedNodeId.textContent = reference;
      range.selectNodeContents(dom.selectedNodeId);
      selection?.removeAllRanges();
      selection?.addRange(range);
      showToast("Select and copy the highlighted stable Wayfinder ID.");
    }
  }

  async function recordCurrentAnswer(event) {
    event.preventDefault();
    if (ui.submitting || ui.session.mode !== "decision-recording" || !ui.session.csrfToken) return;
    const intake = ui.payload?.intake;
    const question = intake?.currentQuestion;
    const revision = Number(dom.recordingRevision.value);
    if (!question || !Number.isSafeInteger(revision) || revision < 0 || revision !== intake.revision) {
      dom.recordingFeedback.textContent = "The intake revision changed. Refresh before recording.";
      return;
    }

    const choiceQuestion = isChoiceQuestion(question);
    let endpoint = ANSWER_ENDPOINT;
    let body;
    if (choiceQuestion) {
      const selected = dom.recordingOptions.querySelector('input[name="intake-choice"]:checked');
      const choice = asText(selected?.value);
      if (!choice || !question.options.some((option) => option.id === choice)) {
        dom.recordingFeedback.textContent = "Select one of the current recorded options.";
        return;
      }
      endpoint = CHOICE_ENDPOINT;
      body = { decision_id: question.decisionId, choice, expected_revision: revision };
    } else {
      const answer = dom.recordingAnswer.value.trim();
      if (!answer || /[\r\n]/.test(answer) || answer.length > question.maxLength) {
        dom.recordingFeedback.textContent = `Enter one line between 1 and ${question.maxLength} characters.`;
        return;
      }
      body = { question_id: question.id, answer, expected_revision: revision };
    }

    ui.submitting = true;
    dom.recordAnswer.disabled = true;
    dom.recordAnswer.setAttribute("aria-busy", "true");
    dom.recordingFeedback.textContent = "Recording against the current revision…";
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "If-Match": `"${revision}"`,
          "X-Wayfinder-CSRF": ui.session.csrfToken,
        },
        cache: "no-store",
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      const result = asObject(await response.json().catch(() => ({})));
      if (!response.ok) {
        if (response.status === 409) {
          await loadState();
          showToast("The route changed before this answer was saved. State was refreshed; review the current question.");
          return;
        }
        dom.recordingFeedback.textContent = compactText(result.error, "The answer was rejected without changing project state.", 240);
        return;
      }
      const refreshed = normalizePayload(result.state);
      ui.payload = refreshed;
      ui.selectedPhaseId = refreshed.currentPhase || refreshed.phases[0]?.id;
      const nextQuestion = refreshed.intake.currentQuestion;
      ui.selectedId = nextQuestion?.decisionId || chooseNeedsYouNode(refreshed.nodes)?.id || null;
      ui.graphHasFramed = false;
      renderAll();
      renderHealth();
      const nextHash = nextQuestion?.decisionId ? `#/decisions/${encodeURIComponent(nextQuestion.decisionId)}` : "#/decisions";
      if (window.location.hash !== nextHash) window.history.replaceState(null, "", nextHash);
      applyRoute(false);
      showToast("Answer recorded atomically. The route now reflects the new canonical revision.");
    } catch (_error) {
      dom.recordingFeedback.textContent = "The local server did not confirm the write. Refresh before trying again.";
    } finally {
      ui.submitting = false;
      dom.recordAnswer.disabled = false;
      dom.recordAnswer.removeAttribute("aria-busy");
    }
  }

  function showToast(message) {
    window.clearTimeout(ui.toastTimer);
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    ui.toastTimer = window.setTimeout(() => {
      dom.toast.hidden = true;
    }, 3200);
  }

  function setMobileNavigation(open, moveFocus = true) {
    const enabled = mobileNavigationQuery.matches;
    const next = enabled && Boolean(open);
    const inspectorModalOpen = modalInspectorQuery.matches && dom.inspector.dataset.open === "true";
    dom.shell.dataset.mobileNav = String(next);
    dom.mobileMenu.setAttribute("aria-expanded", String(next));
    dom.mobileMenu.setAttribute("aria-label", next ? "Hide navigation" : "Show navigation");
    dom.sideRail.inert = (enabled && !next) || inspectorModalOpen;
    if ((enabled && !next) || inspectorModalOpen) {
      dom.sideRail.setAttribute("aria-hidden", "true");
    } else {
      dom.sideRail.removeAttribute("aria-hidden");
    }
    if (next && moveFocus) dom.closeMobileNav.focus();
    dom.mainColumn.inert = next;
    dom.skipLink.inert = next || inspectorModalOpen;
    if (next) {
      dom.mainColumn.setAttribute("aria-hidden", "true");
      dom.skipLink.setAttribute("aria-hidden", "true");
    } else {
      dom.mainColumn.removeAttribute("aria-hidden");
      dom.skipLink.toggleAttribute("aria-hidden", inspectorModalOpen);
    }
    if (moveFocus && !next && enabled) {
      dom.mobileMenu.focus();
    }
  }

  function syncResponsiveNavigation() {
    const inspectorModalOpen = modalInspectorQuery.matches && dom.inspector.dataset.open === "true";
    if (mobileNavigationQuery.matches) {
      setMobileNavigation(dom.shell.dataset.mobileNav === "true", false);
    } else {
      dom.shell.dataset.mobileNav = "false";
      dom.sideRail.inert = inspectorModalOpen;
      dom.sideRail.toggleAttribute("aria-hidden", inspectorModalOpen);
      dom.mainColumn.inert = false;
      dom.mainColumn.removeAttribute("aria-hidden");
      dom.skipLink.inert = inspectorModalOpen;
      dom.skipLink.toggleAttribute("aria-hidden", inspectorModalOpen);
      dom.mobileMenu.setAttribute("aria-expanded", "false");
      dom.mobileMenu.setAttribute("aria-label", "Show navigation");
    }
  }

  function trapMobileNavigationFocus(event) {
    if (event.key !== "Tab" || dom.shell.dataset.mobileNav !== "true" || !mobileNavigationQuery.matches) return;
    const focusable = [...dom.sideRail.querySelectorAll("a[href], button:not([disabled])")]
      .filter((element) => getComputedStyle(element).display !== "none");
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dom.sideRail.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function setInspectorOpen(open, moveFocus = false) {
    const next = Boolean(open);
    const modal = modalInspectorQuery.matches;
    if (modal && !next && document.activeElement instanceof HTMLElement && dom.inspector.contains(document.activeElement)) {
      document.activeElement.blur();
    }
    dom.inspector.dataset.open = String(next);
    if (modal) {
      dom.inspector.inert = !next;
      dom.inspector.toggleAttribute("aria-hidden", !next);
      if (next) {
        dom.inspector.setAttribute("role", "dialog");
        dom.inspector.setAttribute("aria-modal", "true");
      } else {
        dom.inspector.removeAttribute("role");
        dom.inspector.removeAttribute("aria-modal");
      }
    } else {
      dom.inspector.inert = false;
      dom.inspector.removeAttribute("aria-hidden");
      dom.inspector.removeAttribute("role");
      dom.inspector.removeAttribute("aria-modal");
    }
    if (moveFocus && modal && next) dom.closeInspector.focus();
    setInspectorModalIsolation(modal && next);
  }

  function setInspectorModalIsolation(active) {
    const background = [
      dom.projectHeader,
      dom.phaseRoute,
      dom.statusFooter,
      dom.banner,
      dom.routeToolbar,
      document.querySelector("#view-decisions > .view-header"),
      dom.listSurface,
      dom.skipLink,
    ].filter((element) => element instanceof HTMLElement);
    background.forEach((element) => {
      element.inert = active;
      element.toggleAttribute("aria-hidden", active);
    });
    if (active) {
      dom.sideRail.inert = true;
      dom.sideRail.setAttribute("aria-hidden", "true");
    } else {
      syncResponsiveNavigation();
    }
  }

  function inspectorContextSignature() {
    const intake = ui.payload?.intake;
    const revision = Number.isSafeInteger(intake?.revision) ? intake.revision : "none";
    const question = intake?.currentQuestion;
    return `${revision}:${question?.id || "none"}:${question?.decisionId || "none"}`;
  }

  function syncInspectorBreakpoint() {
    if (modalInspectorQuery.matches) {
      setInspectorOpen(false);
    } else {
      setInspectorOpen(
        ui.route === "decisions"
        && Boolean(ui.selectedId || isTextQuestion(ui.payload?.intake.currentQuestion))
        && ui.inspectorDismissedFor !== inspectorContextSignature(),
      );
    }
  }

  function trapInspectorFocus(event) {
    if (event.key !== "Tab" || dom.inspector.dataset.open !== "true" || !modalInspectorQuery.matches) return;
    const focusable = [...dom.inspector.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      .filter((element) => !element.closest("[hidden]") && getComputedStyle(element).display !== "none");
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dom.inspector.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dom.inspector.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  }

  function restoreInspectorOpener() {
    const opener = ui.inspectorOpener;
    ui.inspectorOpener = null;
    if (
      opener instanceof HTMLElement
      && opener.isConnected
      && !opener.closest("[hidden]")
      && !opener.closest("[inert]")
      && !opener.disabled
    ) {
      opener.focus({ preventScroll: false });
      return document.activeElement === opener;
    }
    return false;
  }

  function closeInspector() {
    const previousId = ui.selectedId;
    ui.inspectorDismissedFor = inspectorContextSignature();
    ui.selectedId = null;
    setInspectorOpen(false);
    if (ui.payload) {
      renderFilteredSurfaces();
      renderInspector();
    }
    if (ui.route === "decisions" && parseRoute().nodeId) {
      if (ui.inspectorOpener) ui.pendingFocusTarget = "inspector-opener";
      window.location.hash = "#/decisions";
    } else if (!restoreInspectorOpener()) {
      const previousButton = previousId
        ? document.querySelector(`tr[data-node-id="${CSS.escape(previousId)}"] button`)
        : null;
      if (previousButton instanceof HTMLElement && !previousButton.closest("[hidden]")) {
        previousButton.focus({ preventScroll: false });
      } else {
        document.querySelector('#view-decisions h2[tabindex="-1"]')?.focus({ preventScroll: false });
      }
    }
  }

  function bindEvents() {
    dom.refresh.addEventListener("click", loadState);
    dom.retry.addEventListener("click", loadState);
    dom.mapButton.addEventListener("click", () => setView("map"));
    dom.listButton.addEventListener("click", () => setView("list"));
    dom.railMapLink.addEventListener("click", (event) => {
      event.preventDefault();
      setView("map");
    });
    dom.railDecisionsLink.addEventListener("click", (event) => {
      event.preventDefault();
      setView("list");
    });
    dom.search.addEventListener("input", () => {
      ui.query = dom.search.value;
      renderFilteredSurfaces();
    });
    dom.statusFilters.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-status-filter]");
      if (button) setStatusFilter(button.dataset.statusFilter || "all", button);
    });
    dom.kindFilter.addEventListener("change", () => {
      ui.kind = normalizedToken(dom.kindFilter.value, "all");
      renderFilteredSurfaces();
    });
    dom.phaseList.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-phase-id]");
      if (!button || !ui.payload) return;
      ui.selectedPhaseId = button.dataset.phaseId || ui.selectedPhaseId;
      dom.phaseList.querySelectorAll("button[data-phase-id]").forEach((item) => {
        item.setAttribute("aria-pressed", String(item === button));
      });
      renderCheckpointSummary();
      if (window.location.hash !== "#/checkpoints") window.location.hash = "#/checkpoints";
    });
    dom.zoomIn.addEventListener("click", () => setZoom(ui.zoom + 0.15));
    dom.zoomOut.addEventListener("click", () => setZoom(ui.zoom - 0.15));
    dom.fitMap.addEventListener("click", () => fitGraph());
    dom.focusMap.addEventListener("click", () => focusSelectedGraphNode());
    dom.closeInspector.addEventListener("click", closeInspector);
    dom.openDecision.addEventListener("click", copyDecisionReference);
    dom.openIntake.addEventListener("click", () => {
      const decisionId = ui.payload?.intake.currentQuestion?.decisionId || null;
      ui.inspectorOpener = dom.openIntake;
      ui.inspectorDismissedFor = null;
      ui.selectedId = decisionId;
      renderInspector();
      setInspectorOpen(true, true);
      if (decisionId) window.history.replaceState(null, "", `#/decisions/${encodeURIComponent(decisionId)}`);
      if (!modalInspectorQuery.matches) {
        window.requestAnimationFrame(() => {
          const field = dom.recordingOptions.querySelector("input") || dom.recordingAnswer;
          field?.focus();
          dom.recordingPanel.scrollIntoView({ block: "nearest" });
        });
      }
    });
    dom.recordingForm.addEventListener("submit", recordCurrentAnswer);
    dom.viewAllActivity.addEventListener("click", () => {
      ui.showAllActivity = !ui.showAllActivity;
      renderActivity();
    });
    document.querySelectorAll("[data-activity-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        ui.activityFilter = button.dataset.activityFilter || "all";
        document.querySelectorAll("[data-activity-filter]").forEach((item) => {
          const selected = item === button;
          item.classList.toggle("is-selected", selected);
          item.setAttribute("aria-pressed", String(selected));
        });
        renderActivity();
      });
    });
    dom.collapseRail.addEventListener("click", () => {
      const next = dom.shell.dataset.railCollapsed !== "true";
      dom.shell.dataset.railCollapsed = String(next);
      dom.collapseRail.setAttribute("aria-expanded", String(!next));
      const label = next ? "Expand navigation" : "Collapse navigation";
      dom.collapseRail.setAttribute("aria-label", label);
      dom.collapseRail.title = label;
    });
    dom.mobileMenu.addEventListener("click", () => {
      setMobileNavigation(dom.shell.dataset.mobileNav !== "true");
    });
    dom.closeMobileNav.addEventListener("click", () => setMobileNavigation(false));
    document.querySelectorAll(".rail-link").forEach((link) => {
      link.addEventListener("click", () => {
        if (mobileNavigationQuery.matches) setMobileNavigation(false);
      });
    });
    dom.skipLink.addEventListener("click", (event) => {
      event.preventDefault();
      const heading = document.querySelector(`[data-view="${CSS.escape(ui.route)}"] h2[tabindex="-1"]`);
      heading?.focus();
    });
    window.addEventListener("hashchange", () => applyRoute(true));
    document.addEventListener("keydown", (event) => {
      trapMobileNavigationFocus(event);
      trapInspectorFocus(event);
      if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName || "")) {
        event.preventDefault();
        if (!["map", "decisions"].includes(ui.route)) {
          ui.pendingFocusTarget = "decision-search";
          window.location.hash = "#/decisions";
        } else {
          dom.search.focus();
        }
      }
      if (event.key === "Escape") {
        if (dom.shell.dataset.mobileNav === "true") {
          event.preventDefault();
          setMobileNavigation(false);
        } else if (ui.route === "decisions" && dom.inspector.dataset.open === "true") {
          event.preventDefault();
          closeInspector();
        }
      }
    });
    mobileNavigationQuery.addEventListener("change", syncResponsiveNavigation);
    modalInspectorQuery.addEventListener("change", syncInspectorBreakpoint);

    dom.graph.addEventListener("wheel", (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom(ui.zoom + (event.deltaY < 0 ? 0.1 : -0.1));
    }, { passive: false });
    dom.graph.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest(".graph-node")) return;
      ui.pan = { x: event.clientX, y: event.clientY, offsetX: ui.offsetX, offsetY: ui.offsetY };
      dom.graph.setPointerCapture(event.pointerId);
    });
    dom.graph.addEventListener("pointermove", (event) => {
      if (!ui.pan) return;
      const rect = dom.graph.getBoundingClientRect();
      const scaleX = ui.graphWidth / Math.max(rect.width, 1);
      const scaleY = ui.graphHeight / Math.max(rect.height, 1);
      ui.offsetX = ui.pan.offsetX + (event.clientX - ui.pan.x) * scaleX;
      ui.offsetY = ui.pan.offsetY + (event.clientY - ui.pan.y) * scaleY;
      applyGraphTransform();
    });
    const stopPan = () => { ui.pan = null; };
    dom.graph.addEventListener("pointerup", stopPan);
    dom.graph.addEventListener("pointercancel", stopPan);
  }

  bindEvents();
  syncResponsiveNavigation();
  syncInspectorBreakpoint();
  applyRoute(false);
  loadState();
})();
