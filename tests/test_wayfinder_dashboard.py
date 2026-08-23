from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "skills" / "wayfinder" / "assets" / "dashboard"
ROUTES = ("overview", "map", "decisions", "evidence", "assumptions", "invariants", "checkpoints")


class DashboardHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self.elements.append((tag, dict(attrs)))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)


class WayfinderDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        cls.css = (DASHBOARD / "styles.css").read_text(encoding="utf-8")
        cls.javascript = (DASHBOARD / "app.js").read_text(encoding="utf-8")
        cls.all_source = "\n".join((cls.html, cls.css, cls.javascript))
        cls.parser = DashboardHTMLParser()
        cls.parser.feed(cls.html)
        cls.ids = [attrs["id"] for _, attrs in cls.parser.elements if "id" in attrs]

    def test_dashboard_is_installed_dependency_free_and_private(self) -> None:
        for name in ("index.html", "styles.css", "app.js", "favicon.svg"):
            self.assertTrue((DASHBOARD / name).is_file(), name)
        self.assertIn('href="./styles.css"', self.html)
        self.assertIn('src="./app.js"', self.html)
        self.assertIn('href="./favicon.svg"', self.html)
        banned_client = "ax" + "ios"
        self.assertNotIn(banned_client, self.all_source.lower())
        self.assertNotRegex(self.all_source, r"https?://")
        self.assertNotIn("@import", self.css)
        self.assertNotIn("node_modules", self.all_source)
        self.assertNotIn("/" + "Users/", self.all_source)
        self.assertNotIn("/" + "home/", self.all_source)

    def test_html_ids_are_unique_and_every_sidebar_item_is_a_real_route(self) -> None:
        self.assertEqual(len(self.ids), len(set(self.ids)))
        links = {
            attrs.get("data-route"): attrs
            for tag, attrs in self.parser.elements
            if tag == "a" and attrs.get("class") == "rail-link"
        }
        self.assertEqual(set(ROUTES), set(links))
        for route in ROUTES:
            self.assertEqual(f"#/{route}", links[route]["href"])
            self.assertEqual(route.capitalize(), links[route]["aria-label"])
            self.assertNotIn("aria-current", links[route], "aria-current is assigned only to the active route")
        views = {
            attrs.get("data-view"): attrs
            for tag, attrs in self.parser.elements
            if tag == "section" and attrs.get("data-view")
        }
        self.assertEqual(set(ROUTES), set(views))
        for route, attrs in views.items():
            self.assertEqual(f"{route}-heading", attrs["aria-labelledby"])
            self.assertIn("hidden", attrs)

    def test_hash_router_is_deep_linkable_and_history_accessible(self) -> None:
        self.assertIn('^#\\/(overview|map|decisions|evidence|assumptions|invariants|checkpoints)', self.javascript)
        self.assertIn('(?:D|G)-\\d{3,}', self.javascript)
        self.assertIn('window.addEventListener("hashchange", () => applyRoute(true))', self.javascript)
        self.assertIn('window.history.replaceState(null, "", "#/overview")', self.javascript)
        self.assertIn('const target = `#/decisions/${encodeURIComponent(nodeId)}`', self.javascript)
        self.assertIn("window.location.hash = target", self.javascript)
        self.assertIn('link.setAttribute("aria-current", "page")', self.javascript)
        self.assertIn('link.removeAttribute("aria-current")', self.javascript)
        self.assertIn('heading?.focus({ preventScroll: false })', self.javascript)
        self.assertIn('document.title = `${label} · ${projectTitle} — Wayfinder`', self.javascript)

    def test_landmarks_focus_and_mobile_navigation_contract(self) -> None:
        for fragment in (
            '<a class="skip-link" id="skip-link" href="#route-main">',
            '<aside class="side-rail"',
            'id="close-mobile-nav"',
            '<div class="main-column" id="main-column">',
            '<header class="project-header">',
            '<nav class="phase-route"',
            '<main id="workspace"',
            '<aside class="inspector"',
            '<footer class="status-footer"',
            'aria-live="polite"',
            'aria-label="Search decisions"',
            'role="status"',
        ):
            self.assertIn(fragment, self.html)
        for route in ROUTES:
            self.assertRegex(self.html, rf'<h2 id="{route}-heading" tabindex="-1">')
        self.assertLess(self.html.index('id="close-mobile-nav"'), self.html.index('class="rail-navigation"'))
        self.assertIn(":focus-visible", self.css)
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn("forced-colors", self.css)
        self.assertIn('@media (max-width: 760px)', self.css)
        self.assertIn("setMobileNavigation", self.javascript)
        self.assertIn("trapMobileNavigationFocus", self.javascript)
        self.assertIn("dom.sideRail.inert", self.javascript)
        self.assertIn('dom.closeMobileNav.addEventListener("click", () => setMobileNavigation(false))', self.javascript)
        self.assertIn("dom.closeMobileNav.focus()", self.javascript)
        self.assertIn("dom.mainColumn.inert = next", self.javascript)
        self.assertIn('dom.mainColumn.setAttribute("aria-hidden", "true")', self.javascript)
        self.assertIn('dom.mainColumn.removeAttribute("aria-hidden")', self.javascript)
        self.assertIn("dom.skipLink.inert = next", self.javascript)
        self.assertIn('dom.sideRail.querySelectorAll("a[href], button:not([disabled])")', self.javascript)
        self.assertIn("trapInspectorFocus", self.javascript)
        self.assertIn("input:not([disabled]), textarea:not([disabled])", self.javascript)

    def test_untrusted_payload_has_no_html_or_code_execution_sink(self) -> None:
        unsafe_html_property = "inner" + "HTML"
        self.assertNotIn(unsafe_html_property, self.javascript)
        self.assertNotIn("document.write", self.javascript)
        self.assertNotRegex(self.javascript, r"\beval\s*\(")
        self.assertNotRegex(self.javascript, r"\bFunction\s*\(")
        self.assertIn("textContent", self.javascript)
        self.assertIn("createElement", self.javascript)
        self.assertIn("createElementNS", self.javascript)
        self.assertIn(r"\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}", self.javascript)
        self.assertIn("dom.openDecision.dataset.reference = stableReference", self.javascript)
        self.assertNotIn("node.path || node.id", self.javascript)

    def test_all_seven_views_have_real_data_renderers_and_meaningful_empty_states(self) -> None:
        for function in (
            "renderOverview",
            "renderGraph",
            "renderList",
            "renderEvidence",
            "renderAssumptions",
            "renderInvariants",
            "renderCheckpoints",
        ):
            self.assertIn(f"function {function}", self.javascript)
        for payload_key in ("evidence", "assumptions", "invariants", "milestones", "intake", "implementationBaseline"):
            self.assertIn(f"{payload_key}:", self.javascript)
        for message in (
            "No evidence references yet",
            "No assumptions are exposed in this route",
            "No active invariants are exposed",
            "No milestone state available",
            "No decisions or gates have been recorded",
            "No route nodes yet",
        ):
            self.assertIn(message, self.all_source)
        self.assertIn('.workspace[aria-busy="true"] .route-main::after', self.css)
        self.assertIn("skeleton-pulse", self.css)
        for state_name in ("loading", "error", "empty", "degraded"):
            self.assertIn(f'renderSurfaceState("{state_name}"', self.javascript)

    def test_controls_are_live_and_no_anchor_is_a_placeholder(self) -> None:
        anchors = [attrs for tag, attrs in self.parser.elements if tag == "a"]
        for attrs in anchors:
            href = attrs.get("href", "")
            self.assertTrue(href.startswith("#/" ) or href == "#route-main", href)
            self.assertNotEqual("#", href)
            self.assertFalse(href.lower().startswith("javascript:"))
        buttons = [attrs for tag, attrs in self.parser.elements if tag == "button"]
        event_delegated = {"status-filters"}
        form_submits = {"record-answer"}
        for attrs in buttons:
            if "data-status-filter" in attrs or "data-activity-filter" in attrs:
                continue
            control_id = attrs.get("id")
            self.assertIsNotNone(control_id, attrs)
            if control_id in event_delegated or control_id in form_submits:
                continue
            self.assertIn(f'byId("{control_id}")', self.javascript)
        for control in ("refresh", "retry", "mapButton", "listButton", "zoomIn", "zoomOut", "fitMap", "focusMap", "closeInspector", "openDecision", "openIntake", "viewAllActivity", "collapseRail", "mobileMenu", "closeMobileNav"):
            self.assertIn(f"dom.{control}.addEventListener", self.javascript)
        self.assertIn('dom.recordingForm.addEventListener("submit", recordCurrentAnswer)', self.javascript)
        self.assertIn('dom.recordingPanel.scrollIntoView({ block: "nearest" })', self.javascript)

    def test_shortcut_dismissal_and_filtered_empty_state_are_truthful(self) -> None:
        self.assertIn('ui.pendingFocusTarget = "decision-search"', self.javascript)
        self.assertIn('const focusDecisionSearch = ui.pendingFocusTarget === "decision-search"', self.javascript)
        self.assertIn('dom.search.focus({ preventScroll: false })', self.javascript)
        self.assertNotIn('window.requestAnimationFrame(() => dom.search.focus())', self.javascript)
        self.assertIn("inspectorDismissedFor", self.javascript)
        self.assertIn("inspectorContextSignature", self.javascript)
        self.assertIn("ui.inspectorDismissedFor = inspectorContextSignature()", self.javascript)
        self.assertIn('`No ${ui.activityFilter} match this filter.`', self.javascript)
        self.assertIn("inspectorOpener", self.javascript)
        self.assertIn("restoreInspectorOpener", self.javascript)
        self.assertIn('ui.pendingFocusTarget = "inspector-opener"', self.javascript)

    def test_filtered_map_controls_and_gate_statuses_follow_visible_state(self) -> None:
        self.assertIn('const statusMatches = ui.status === "all" || nodeStatusGroup(node) === ui.status;', self.javascript)
        self.assertNotIn('node.kind !== "gate" && nodeStatusGroup(node)', self.javascript)
        self.assertIn('ui.graphMode = "empty"', self.javascript)
        self.assertIn('dom.graphModeLabel.textContent = ui.payload.nodes.length ? "No visible route nodes" : "No route nodes yet"', self.javascript)
        self.assertIn("const hasVisibleNodes = ui.graphPositions.size > 0", self.javascript)
        self.assertIn("dom.fitMap.disabled = !hasVisibleNodes", self.javascript)
        self.assertIn('if (!ui.graphPositions.size) return;', self.javascript)

    def test_mobile_inspector_isolates_background_and_restores_its_opener(self) -> None:
        self.assertIn("setInspectorModalIsolation(modal && next)", self.javascript)
        self.assertIn("dom.projectHeader", self.javascript)
        self.assertIn("dom.phaseRoute", self.javascript)
        self.assertIn("dom.listSurface", self.javascript)
        self.assertIn("element.inert = active", self.javascript)
        self.assertIn('element.toggleAttribute("aria-hidden", active)', self.javascript)
        self.assertIn("dom.sideRail.inert = true", self.javascript)
        self.assertIn("ui.inspectorOpener = dom.openIntake", self.javascript)

    def test_decision_recording_ui_is_narrow_cas_bound_and_truthful(self) -> None:
        self.assertIn('const CHOICE_ENDPOINT = "./api/intake/choice"', self.javascript)
        self.assertIn('const ANSWER_ENDPOINT = "./api/intake/answer"', self.javascript)
        self.assertIn('const SESSION_ENDPOINT = "./api/session"', self.javascript)
        self.assertIn('"If-Match": `"${revision}"`', self.javascript)
        self.assertIn('"X-Wayfinder-CSRF": ui.session.csrfToken', self.javascript)
        self.assertIn('body = { decision_id: question.decisionId, choice, expected_revision: revision }', self.javascript)
        self.assertIn('body = { question_id: question.id, answer, expected_revision: revision }', self.javascript)
        self.assertIn("revision !== intake.revision", self.javascript)
        self.assertIn("This launch is read-only", self.javascript)
        self.assertIn("Recorded locally as <strong>User</strong> via Dashboard", self.html)
        self.assertIn('id="recording-why"', self.html)
        self.assertIn("why: compactText(rawQuestion.why", self.javascript)
        self.assertNotIn("input.checked = true", self.javascript)
        self.assertIn("Select one of the current recorded options", self.javascript)
        self.assertNotIn('name="actor"', self.html)
        self.assertNotIn('name="source"', self.html)

    def test_status_and_relationships_are_not_expressed_by_color_alone(self) -> None:
        for token in ("statusLabel", "gateStatusLabel", "nodeStatusLabel", "status-symbol", "data-status"):
            self.assertIn(token, self.javascript)
        self.assertIn("graph-legend", self.html)
        self.assertIn("Edge types", self.html)
        self.assertIn('node.kind === "gate" ? `Gate · ${nodeStatusLabel(node)}`', self.javascript)
        self.assertIn("counts: calculatedCounts", self.javascript)
        self.assertIn('`${counts.decisions} decisions · ${counts.gates} gates`', self.javascript)

    def test_overview_distinguishes_workstreams_comparison_level_and_baseline(self) -> None:
        for control_id in (
            "primary-workstream",
            "secondary-workstreams",
            "comparison-level",
            "comparison-title",
            "implementation-baseline",
            "baseline-revision",
        ):
            if control_id == "implementation-baseline":
                self.assertIn("Implementation baseline", self.html)
            else:
                self.assertIn(f'id="{control_id}"', self.html)
        self.assertIn("primary_domain || rawDomain.selected", self.javascript)
        self.assertIn("proposed: asText(rawDomain.proposed)", self.javascript)
        self.assertIn("proposed · awaiting confirmation", self.javascript)
        self.assertIn("secondary_workstreams", self.javascript)
        self.assertIn("comparison.comparison_level || comparison.level || comparison.kind", self.javascript)
        self.assertIn('"named_technology"', self.javascript)
        self.assertIn("comparisons.forEach((comparison)", self.javascript)
        self.assertNotIn("intake.comparisons[0]", self.javascript)
        for exact_public_key in (
            "mvp_speed",
            "scale_beyond_mvp",
            "security_privacy",
            "team_fit",
            "reversibility",
            "evidence_refs",
            "primary_sources",
        ):
            self.assertIn(exact_public_key, self.javascript)
        self.assertIn("architecture or operating-strategy comparisons", self.javascript.lower())
        self.assertIn("Destination revision", self.javascript)
        self.assertIn("applicableDecisions", self.javascript)
        self.assertIn("manifest_hash", self.javascript)
        self.assertIn("Manifest hash", self.javascript)
        self.assertIn("^[a-f0-9]{64}$", self.javascript)

    def test_checkpoint_copy_is_domain_neutral_and_rerun_specific(self) -> None:
        self.assertIn('"Ready for execution"', self.javascript)
        self.assertIn("planning exit proof", self.javascript.lower())
        self.assertIn("rawExit.planning_exit_ready", self.javascript)
        self.assertNotIn("Ready for spec", self.all_source)
        self.assertNotIn("Pre-spec", self.all_source)
        for label in ("Run now", "Rerun if changed", "Run at checkpoint"):
            self.assertIn(label, self.javascript)
        self.assertIn("Use these milestones to know when Wayfinder should run again", self.html)

    def test_visual_system_is_restrained_tactile_and_single_column_on_mobile(self) -> None:
        self.assertIn("--accent: #d88b00", self.css)
        self.assertIn("--accent-strong: #8a5600", self.css)
        self.assertNotRegex(self.css.lower(), r"\b(purple|neon)\b")
        self.assertIn("grid-template-columns: minmax(0, 1.35fr) minmax(340px, 0.8fr)", self.css)
        self.assertIn("transform: translateY(1px)", self.css)
        transitions = re.findall(r"transition:\s*([^;]+);", self.css)
        self.assertTrue(transitions)
        for transition in transitions:
            self.assertRegex(transition, r"^(?:none|(?:(?:transform|opacity)[^,]*(?:,\s*)?)+)$", transition)
        mobile_navigation = self.css.split("@media (max-width: 760px)", 1)[1].split("@media (max-width: 460px)", 1)[0]
        self.assertIn(".mobile-nav-close {", mobile_navigation)
        self.assertIn("display: inline-grid", mobile_navigation)
        self.assertRegex(self.css, r"\.mobile-nav-close\s*\{\s*display:\s*none;")
        mobile = self.css.rsplit("@media (max-width: 760px)", 1)[1]
        self.assertIn(".overview-hero { display: grid; grid-template-columns: minmax(0, 1fr); }", mobile)
        self.assertIn(".record-grid,", mobile)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", mobile)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)

    def test_small_mobile_decision_table_has_no_horizontal_dependency(self) -> None:
        mobile = self.css.split("@media (max-width: 460px)", 1)[1].split("@media (prefers-reduced-motion", 1)[0]
        self.assertIn(".table-scroll", mobile)
        self.assertIn("min-width: 0", mobile)
        self.assertIn("table-layout: fixed", mobile)
        for hidden_column in (3, 5, 6):
            self.assertIn(f"th:nth-child({hidden_column})", mobile)
            self.assertIn(f"td:nth-child({hidden_column})", mobile)
        self.assertIn("overflow-x: hidden", mobile)

    def test_desktop_decision_list_and_inspector_share_one_explicit_grid_row(self) -> None:
        list_rule = re.search(r"\.decision-workspace \.list-surface \{([^}]+)\}", self.css)
        inspector_rule = re.search(r"\.decision-workspace \.inspector \{([^}]+)\}", self.css)
        self.assertIsNotNone(list_rule)
        self.assertIsNotNone(inspector_rule)
        self.assertIn("grid-column: 1", list_rule.group(1))
        self.assertIn("grid-row: 1", list_rule.group(1))
        self.assertIn("grid-column: 2", inspector_rule.group(1))
        self.assertIn("grid-row: 1", inspector_rule.group(1))
        self.assertIn("#close-inspector {\n  display: none;", self.css)
        mobile = self.css.rsplit("@media (max-width: 900px)", 1)[1]
        self.assertIn("#close-inspector { display: inline-grid; }", mobile)

    def test_default_map_focus_has_a_legible_full_route_escape_hatch(self) -> None:
        self.assertIn("FOCUSED_NODE_HEIGHT", self.javascript)
        self.assertIn("focusSelectedGraphNode", self.javascript)
        self.assertIn('dom.graphModeLabel.textContent = "Full route overview"', self.javascript)
        self.assertIn('dom.graphModeLabel.textContent = `Focused route · ${ui.selectedId}`', self.javascript)
        self.assertIn("Fit map reveals the full route", self.html)


if __name__ == "__main__":
    unittest.main()
