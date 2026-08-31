# React Panel Visuals and Three.js Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved midnight network control-deck visual system and add two lazy, bounded React Three Fiber scenes without compromising accessibility, performance, or core workflows.

**Architecture:** CSS semantic tokens and Motion provide the primary visual language. Three.js is isolated behind one capability gate and two dynamically imported scenes; ordinary DOM and SVG fallbacks remain authoritative for forms, data, navigation, and status.

**Tech Stack:** Existing React/Vite stack, Motion 13.1.1, Three.js 0.185.1, React Three Fiber 9.7.0, Vitest, Playwright, screenshot and bundle-budget checks

**Spec:** `docs/superpowers/specs/2026-08-30-react-vite-panel-redesign.md`

## Global Constraints

- Three.js is allowed only on the public/login background and admin overview header.
- Do not include real IPs, hosts, usernames, credentials, or raw operational identifiers in GPU buffers, scene names, logs, or canvas accessibility text.
- Do not mount WebGL below 768 CSS pixels, under reduced motion, without WebGL, or after a 1.5-second initialization timeout.
- Cap Three.js at 30 FPS; DPR is at most 1.5 desktop and 1.0 below 768 CSS pixels.
- Pause while hidden, dispose resources on unmount, and ship no post-processing pipeline.
- Every canvas has a CSS/SVG fallback and duplicates no unique information.
- Preserve all Phase 2 unit, browser, accessibility, and performance gates.

---

## File Structure

| Path | Responsibility |
|---|---|
| `frontend/src/visuals/capabilities.ts` | reduced-motion, viewport, visibility, WebGL, timeout decisions |
| `frontend/src/visuals/SceneBoundary.tsx` | lazy import, timeout, error, fallback, lifecycle |
| `frontend/src/visuals/network-field/` | deterministic decorative public/login scene |
| `frontend/src/visuals/protocol-constellation/` | aggregate admin protocol scene |
| `frontend/src/styles/visuals.css` | static grid, glow, fallback, canvas containment |
| `frontend/tests/visuals-*.test.tsx` | capability, geometry, fallback, no-secret tests |
| `frontend/e2e/visuals.spec.ts` | reduced-motion/mobile/error/performance browser checks |

### Task 1: Visual tokens, density, and motion preference

**Files:**
- Modify: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/styles/layout.css`
- Modify: `frontend/src/styles/components.css`
- Create: `frontend/src/styles/visuals.css`
- Create: `frontend/src/lib/motion/preference.ts`
- Create: `frontend/src/lib/motion/transitions.ts`
- Modify: `frontend/src/layouts/AdminLayout.tsx`
- Modify: `frontend/src/layouts/PublicLayout.tsx`
- Create: `frontend/tests/visual-system.test.tsx`

**Interfaces:**
- Produces: `useMotionPreference()`, `panelTransition`, `dialogTransition`, finalized semantic visual tokens

- [ ] **Step 1: Write failing preference and semantic-token tests**

```ts
it("system reduced motion wins over the optional full-motion preference", () => {
  matchMediaMock("(prefers-reduced-motion: reduce)", true)
  localStorage.setItem("hy2.motion", "full")
  expect(resolveMotionPreference()).toBe("reduced")
})

it("defines visible focus and all state tokens", () => {
  const css = readCss("src/styles/tokens.css")
  for (const token of ["--focus-ring", "--state-success", "--state-warning", "--state-danger"]) {
    expect(css).toContain(token)
  }
})
```

- [ ] **Step 2: Run and confirm missing motion modules**

Run: `cd frontend && npm test -- visual-system.test.tsx`

Expected: collection fails.

- [ ] **Step 3: Implement preference and transition primitives**

```ts
export function resolveMotionPreference(): "full" | "reduced" {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return "reduced"
  return localStorage.getItem("hy2.motion") === "reduced" ? "reduced" : "full"
}

export const panelTransition = {duration: 0.22, ease: [0.22, 1, 0.36, 1]} as const
export const dialogTransition = {type: "spring", stiffness: 360, damping: 32} as const
```

Use Motion only for stateful entrances/layout changes. Unchanged polling payloads retain stable component keys and do not replay animation.

- [ ] **Step 4: Finish grid, glow, hierarchy, and responsive styling**

Add static radial/grid layers with pseudo-elements, luminous 1px semantic borders, mono numeric styles, status shapes, and dense responsive cards. All copy remains above opaque-enough surfaces to meet contrast.

- [ ] **Step 5: Run unit, accessibility, and build checks**

Run: `cd frontend && npm test -- visual-system.test.tsx layout-accessibility.test.tsx && npm run typecheck && npm run lint && npm run build`

Expected: all pass.

- [ ] **Step 6: Commit primary visual system**

```bash
git add frontend/src/styles frontend/src/lib/motion frontend/src/layouts frontend/tests/visual-system.test.tsx
git commit -m "feat(frontend): apply network control deck visual system"
```

### Task 2: Three.js dependency and capability boundary

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `frontend/src/visuals/capabilities.ts`
- Create: `frontend/src/visuals/SceneBoundary.tsx`
- Create: `frontend/src/visuals/StaticNetworkFallback.tsx`
- Create: `frontend/tests/visuals-capabilities.test.tsx`

**Interfaces:**
- Produces: `sceneCapability()`, `useSceneCapability()`, `<SceneBoundary load fallback label />`

- [ ] **Step 1: Add exact Three.js dependencies**

```json
{
  "dependencies": {
    "@react-three/fiber": "9.7.0",
    "three": "0.185.1"
  },
  "devDependencies": {
    "@types/three": "0.185.4"
  }
}
```

Run: `cd frontend && npm install --ignore-scripts`

Expected: lockfile changes only for the added dependency graph.

- [ ] **Step 2: Write failing capability/fallback tests**

```tsx
it.each([
  [{reduced: true, width: 1440, webgl: true}, "reduced-motion"],
  [{reduced: false, width: 767, webgl: true}, "narrow-viewport"],
  [{reduced: false, width: 1440, webgl: false}, "webgl-unavailable"],
])("rejects unsupported scene capability", (input, reason) => {
  expect(sceneCapability(input)).toEqual({enabled: false, reason})
})
```

- [ ] **Step 3: Run and confirm missing boundary**

Run: `cd frontend && npm test -- visuals-capabilities.test.tsx`

Expected: collection fails.

- [ ] **Step 4: Implement deterministic capability and lazy boundary**

```ts
export function sceneCapability(input: SceneEnvironment): SceneDecision {
  if (input.reduced) return {enabled: false, reason: "reduced-motion"}
  if (input.width < 768) return {enabled: false, reason: "narrow-viewport"}
  if (!input.webgl) return {enabled: false, reason: "webgl-unavailable"}
  return {enabled: true, reason: "ready"}
}
```

`SceneBoundary` starts a 1,500ms timer, renders `StaticNetworkFallback` until the lazy module calls `onReady`, catches import/render errors, and unmounts the scene on hidden/reduced/narrow transitions.

- [ ] **Step 5: Run tests, inspect chunks, and commit**

Run: `cd frontend && npm test -- visuals-capabilities.test.tsx && npm run typecheck && npm run build`

Expected: all pass; Three/R3F appears only in lazy chunks, not the initial shared chunk.

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/visuals frontend/tests/visuals-capabilities.test.tsx
git commit -m "feat(frontend): add bounded Three scene loader"
```

### Task 3: Public and login network field

**Files:**
- Create: `frontend/src/visuals/network-field/geometry.ts`
- Create: `frontend/src/visuals/network-field/NetworkField.tsx`
- Create: `frontend/src/visuals/network-field/NetworkFieldScene.tsx`
- Modify: `frontend/src/features/public/HomePage.tsx`
- Modify: `frontend/src/features/auth/LoginPage.tsx`
- Create: `frontend/tests/visuals-network-field.test.tsx`

**Interfaces:**
- Produces: deterministic `buildNetwork(seed)`, lazy `<NetworkField />`

- [ ] **Step 1: Add deterministic geometry and redaction tests**

```ts
it("creates a stable bounded decorative graph", () => {
  const graph = buildNetwork(0x485932)
  expect(graph.nodes).toHaveLength(42)
  expect(graph.edges.length).toBeGreaterThanOrEqual(48)
  expect(graph.edges.length).toBeLessThanOrEqual(72)
  expect(JSON.stringify(graph)).not.toMatch(/alice|token|\d{1,3}(\.\d{1,3}){3}/i)
})
```

- [ ] **Step 2: Run and confirm geometry module is absent**

Run: `cd frontend && npm test -- visuals-network-field.test.tsx`

Expected: collection fails.

- [ ] **Step 3: Implement graph and scene**

Use a seeded xorshift generator for 42 nodes inside a fixed radius and connect nearest non-duplicate neighbors until 60 edges. Render points and one line-segment buffer; do not allocate one object per edge. Pointer input offsets the camera by at most 0.18 world units.

```tsx
<Canvas dpr={[1, 1.5]} gl={{alpha: true, antialias: false, powerPreference: "low-power"}}>
  <NetworkFieldScene onReady={onReady} />
</Canvas>
```

The animation loop skips frames until at least `1 / 30` seconds elapsed and invalidates nothing while hidden.

- [ ] **Step 4: Mount behind ordinary DOM content**

Home and login render the same lazy background boundary. The login card remains a DOM form with its own opaque background, labels, focus order, and status live region.

- [ ] **Step 5: Run tests and inspect public/login build**

Run: `cd frontend && npm test -- visuals-network-field.test.tsx auth-user-flows.test.tsx && npm run typecheck && npm run lint && npm run build`

Expected: all pass.

- [ ] **Step 6: Commit network field**

```bash
git add frontend/src/visuals/network-field frontend/src/features/public frontend/src/features/auth frontend/tests/visuals-network-field.test.tsx
git commit -m "feat(frontend): add decorative network field"
```

### Task 4: Admin protocol constellation

**Files:**
- Create: `frontend/src/visuals/protocol-constellation/types.ts`
- Create: `frontend/src/visuals/protocol-constellation/model.ts`
- Create: `frontend/src/visuals/protocol-constellation/ProtocolConstellation.tsx`
- Create: `frontend/src/visuals/protocol-constellation/ProtocolConstellationScene.tsx`
- Modify: `frontend/src/features/admin/overview/AdminOverviewPage.tsx`
- Create: `frontend/tests/visuals-protocol-constellation.test.tsx`

**Interfaces:**
- Produces: `ProtocolAggregate`, `normalizeProtocolAggregate`, lazy constellation with DOM summary

- [ ] **Step 1: Add aggregate normalization and accessible-summary tests**

```tsx
it("normalizes zero traffic without NaN", () => {
  expect(normalizeProtocolAggregate({hysteria: 0, xray: 0, tuic: 0})).toEqual({
    hysteria: 0, xray: 0, tuic: 0, total: 0,
  })
})

it("shows protocol values outside canvas", () => {
  render(<ProtocolConstellation aggregate={{hysteria: 10, xray: 20, tuic: 5}} />)
  expect(screen.getByText("Hysteria")).toBeVisible()
  expect(screen.getByText("Xray")).toBeVisible()
  expect(screen.getByText("TUIC")).toBeVisible()
})
```

- [ ] **Step 2: Run and observe missing modules**

Run: `cd frontend && npm test -- visuals-protocol-constellation.test.tsx`

Expected: collection fails.

- [ ] **Step 3: Implement aggregate-only scene**

Render three fixed clusters whose radius and emissive intensity derive from normalized aggregate proportions. Clamp all inputs to safe finite non-negative numbers. Scene object names are literal protocol labels only; no user-level data enters props or buffers.

- [ ] **Step 4: Mount in overview with equal DOM representation**

The overview hero contains numeric protocol cards and the optional scene. Query failure or scene failure leaves cards unchanged. Reduced motion uses a static SVG orbit fallback.

- [ ] **Step 5: Run tests and commit**

Run: `cd frontend && npm test -- visuals-protocol-constellation.test.tsx admin-users.test.tsx && npm run typecheck && npm run lint && npm run build`

Expected: all pass.

```bash
git add frontend/src/visuals/protocol-constellation frontend/src/features/admin/overview frontend/tests/visuals-protocol-constellation.test.tsx
git commit -m "feat(frontend): add protocol constellation"
```

### Task 5: Visual performance, accessibility, and regression gate

**Files:**
- Create: `frontend/scripts/check-bundle-budget.mjs`
- Modify: `frontend/package.json`
- Create: `frontend/e2e/visuals.spec.ts`
- Create: `frontend/tests/visuals-cleanup.test.tsx`
- Modify: `scripts/hy2-preflight.sh`

**Interfaces:**
- Produces: `npm run check:bundle`, automated reduced-motion/mobile/WebGL-failure/cleanup gate

- [ ] **Step 1: Add failing resource cleanup test**

```tsx
it("disposes geometry and material on unmount", () => {
  const geometry = {dispose: vi.fn()}
  const material = {dispose: vi.fn()}
  const {unmount} = renderSceneWithResources(geometry, material)
  unmount()
  expect(geometry.dispose).toHaveBeenCalledOnce()
  expect(material.dispose).toHaveBeenCalledOnce()
})
```

- [ ] **Step 2: Implement build-budget checker**

Read `dist/.vite/manifest.json`, gzip entry JS/CSS in memory, and fail when shared initial JS exceeds 220 KiB or shared CSS exceeds 80 KiB. Assert no `.map`, `three`, or `react-three-fiber` module appears in the entry's static imports.

```js
if (entryJsGzip > 220 * 1024) throw new Error(`initial JS ${entryJsGzip} exceeds 220 KiB`)
if (entryCssGzip > 80 * 1024) throw new Error(`shared CSS ${entryCssGzip} exceeds 80 KiB`)
```

- [ ] **Step 3: Add browser fallbacks and frame checks**

Playwright covers 767px fallback, reduced-motion fallback, disabled WebGL, delayed import timeout, page visibility pause, no console errors, keyboard login, and DOM protocol summaries. Use a 10-second trace window to assert the scene's test-only frame counter stays at or below 305 frames.

- [ ] **Step 4: Run the complete visual and parity gates**

Run: `cd frontend && npm run typecheck && npm run lint && npm run test && npm run build && npm run check:bundle && npm run e2e -- visuals.spec.ts`

Expected: all pass.

- [ ] **Step 5: Wire preflight and commit visual phase**

Add `npm run check:bundle` after the production build in preflight.

```bash
git add frontend scripts/hy2-preflight.sh
git commit -m "test(frontend): enforce visual performance budgets"
```

## Phase Acceptance

- The control-deck design is consistent across all pages and passes contrast/keyboard checks.
- Only the two approved surfaces load Three.js, and both are lazy.
- Reduced motion, narrow viewport, missing WebGL, timeout, and import failure render static fallbacks.
- The initial app bundle excludes Three.js and passes the 220 KiB/80 KiB budgets.
- No real identity, host, IP, or credential enters a scene.
- All functional parity tests continue to pass.
