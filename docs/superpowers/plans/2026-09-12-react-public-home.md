# React public home implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Render the existing public home in React without changing its content, visual design, illustrative data or interaction behavior.

**Architecture:** Add a public feature component to the established built React application. The controlled preview serves `/__react/`; `/` stays legacy until the separate cutover gate. Home owns its page DOM and uses no administrator API or runtime data.

**Tech Stack:** The exact React, TypeScript and Vite pins already adopted by the logs slice; existing local CSS/fonts and Playwright. No additional framework or component library.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Preserve existing CSS cascade and design tokens initially; replace DOM ownership with components, not a default component-library theme.
- During coexistence, one frontend owns each page's DOM. Do not run legacy polling and React polling against the same rendered page.
- Keep existing URLs, subscription formats, dedicated-user link exchanges, cookie separation, redirects, downloads, QR responses and status semantics.
- Missing API/assets/subscription resources never become a successful SPA page.
- No production routing, runtime state, authentication behavior, deployment scripts, public content or new product features change in this task.
- The homepage's chart, users and health are explicitly illustrative. Never fetch private data to populate them or present them as live status.

## Source contract

`hysteria/public_views.py:render_home` is the authoritative markup/content; `hysteria/static/home.js` is the behavior reference. Preserve the actual `site-*` design, not the older unused `home-*` design also present in the stylesheet. The body classes are `page-home page-site`; title is `Hysteria · 连接网络，掌控全局`. Use the already linked `/static/style.css` and existing local fonts. Do not introduce another CSS file or load legacy home.js/ui-core.js on the React page.

The three example panels are traffic/users/health. Initial `#demo-users` or `#demo-health` selects its panel; unknown/absent hash selects traffic. Tab clicks prevent anchor navigation, matching the current behavior. ArrowRight/ArrowLeft wrap, Home/End select first/last, Space selects the focused tab; keyboard activation moves focus. Exactly one tab has `tabIndex=0`, `aria-selected=true`, and one visible tabpanel. Other page anchors and `/login` remain ordinary links.

Reveal behavior observes `.site-feature` and `.site-console` with threshold `0.1`, adds `site-reveal` once visible and unobserves that element. Do not observe when reduced motion is requested or IntersectionObserver is unavailable; content remains visible. Disconnect on pagehide and component cleanup. No animation library is needed.

### Task 1: Public home with current content and accessible preview tabs

**Prerequisite:** The logs slice Task 2 is reviewed and complete. Do not work concurrently in its frontend/preview files.

**Files:** Create `frontend/src/features/public/HomePage.tsx`, `frontend/src/features/public/ConsolePreview.tsx`, `frontend/README.md` and `tests/react_home_browser.cjs`. Modify `frontend/src/main.tsx`, `tests/react_preview_server.py`, `tests/run_react_browser.py`, `tests/react_logs_browser.cjs`, its focused Python preview tests, and package.json only if needed to include the new browser test. Do not modify legacy renderers, scripts or styles.

**Interfaces:** `HomePage` is a no-props public React component. `ConsolePreview` owns only the three illustrative panel selection/focus states. The existing main entry chooses the home component only for the exact controlled route `/__react/`; the exact logs route remains unchanged. The preview serves the same manifest-selected built entry for these two explicit routes and sets the correct title/body classes without giving unknown paths a fallback. Client code chooses its page from the same exact path, not substring matching. No `/api/v1/session` call is needed for home. The current `applyInitialShellPreferences()` call belongs only to the logs entry; home must not inherit `has-shell` or sidebar state/classes from that initialization.

- [ ] Write the browser regression before components. Use the existing isolated built preview and assert the controlled route is 200, the title/body classes match, and public content loads without a session. Run it and record the intended missing-page failure before implementation.

```js
const response = await page.goto(baseUrl + '/__react/#demo-users');
assert.equal(response.status(), 200);
assert.equal(await page.title(), 'Hysteria · 连接网络，掌控全局');
assert.equal(await page.locator('#demo-tab-users').getAttribute('aria-selected'), 'true');
assert.equal(await page.locator('#demo-users').isVisible(), true);
assert.equal(await page.locator('#demo-traffic').isVisible(), false);
await page.locator('#demo-tab-users').focus();
await page.keyboard.press('ArrowRight');
assert.equal(await page.locator('#demo-tab-health').getAttribute('aria-selected'), 'true');
assert.equal(await page.locator('#demo-tab-health').evaluate(el => el === document.activeElement), true);
```

- [ ] Convert the existing home document into JSX components without raw HTML injection. Preserve all wording, tag order, classes, SVG geometry, example values, ARIA labels, meter attributes and link destinations. Use JSX SVG attribute spellings while preserving rendered attributes. Keep the public header/footer separate from AdminShell; no admin sidebar or private badge belongs on this page.

```tsx
type Demo = 'traffic' | 'users' | 'health';
const demos: readonly Demo[] = ['traffic', 'users', 'health'];
function initialDemo(): Demo {
  return demos.find(demo => window.location.hash === `#demo-${demo}`) ?? 'traffic';
}
```

- [ ] Implement controlled tabs and scoped reveal lifecycle in React. Do not import or execute the legacy DOM script. Verify all three initial hashes plus unknown hash, click activation, ArrowLeft/ArrowRight wrapping, Home/End, Space and focus. Confirm clicking a tab does not change the current URL hash and does not jump the page, matching the existing script. Confirm decorative topology still has its descriptive role/label and demo data still has visible example disclaimers.

- [ ] Extend the strict preview allowlist for the exact home entry only. Test `/__react/missing`, `/__react/admin/missing`, missing built assets, `/api/v1/missing`, and retired Codex URLs remain 404; HEAD has no body. Both public entries `/` and `/__react/` must remain available for paired comparison. The existing logs authentication checks must still run.

- [ ] Compare SSR and React at widths 1920, 1024, 390 with the same fonts, browser, reduced-motion setting and selected tab. Assert headings, visible link labels/destinations, example values, main content/hero/topology/card/console bounding boxes, body background and no horizontal overflow. Capture paired full-page screenshots in each tab at desktop and the default tab at all three widths. Do not assert only that the page exists.

```js
assert.equal(await page.locator('.site-demo-label').innerText(), '界面示意 · 非实时数据');
assert.equal(await page.locator('.site-demo-panel:visible').count(), 1);
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
```

- [ ] Collect page errors, unexpected failed requests and all `/api/` requests during home tests: the latter must be empty. Confirm no legacy home/shell/ui-core script loads. Test reduced motion and absent IntersectionObserver leave content visible; test normal motion adds reveal on scroll. Re-run the existing logs browser test to catch shared-entry regressions.

- [ ] Close the shared-harness minor findings from the approved logs review. In `verifyAuthenticatedLogs`, explicitly wait for `preview-admin` before collecting initial column/cell contents. Attach `collectFailures` to every auth/recovery/keyboard and comparison page; intentional 401/503/aborted requests are allowlisted by exact route/status and scenario, not blanket ignored. Unexpected script errors and asset failures must fail every scenario. Run the logs browser acceptance with deliberately delayed initial logs to demonstrate the row wait is effective, while retaining the timeout/retry regression.

- [ ] Write `frontend/README.md` with exact `npm run typecheck:react`, `npm run build:react`, `npm run test:react-browser`, and Python-venv prerequisite instructions. Document both controlled entries, unchanged public routes, shared CSS/fonts, real cookie-based read API, no production cutover, and the preview's POST 405 behavior including logout. Remove only `.superpowers/sdd/2026-09-12-react-logs-slice/task-2-report.md` from the git index using `git rm --cached -- ...`; retain the local report for workflow continuity. Permanent usage docs belong in README rather than force-tracked ignored scratch storage.

- [ ] Run strict typecheck, production build, the updated frontend quality entry, focused preview isolation/public route tests, and CSS build consistency. Record commands/results and screenshot paths. Commit only owned files locally after review; no push or deployment.

## Self-review and scope remainder

This slice covers the public parity-register row only. It deliberately leaves login/session mutations, user panel and administrative pages to their respective API-backed slices, without removing any old implementation. The exact-path entry and shared static release preserve coexistence. Public content has no backend state dependency, so adding a home JSON API would duplicate static presentation with no migration benefit.
