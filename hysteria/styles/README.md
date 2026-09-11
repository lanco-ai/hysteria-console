# Stylesheet ownership

Edit the source section that owns a component, then run `npm run build:css`.
`hysteria/admin.css` is the checked-in deployment bundle; do not edit it directly.
`npm run check:css-build` rejects stale bundles without changing files.

`manifest.json` is the explicit cascade order. Initial extraction preserved every
byte and rule ordering. The numerical prefixes document that order; do not reorder
sections to make their names look cleaner without browser regression coverage.

- `01`: public entry refinements; `02`: tokens, fonts and base elements.
- `03`: buttons, forms, typography and utilities.
- `04–05`: home and authentication pages.
- `06–09`: shell, overview/users, dialogs/status and management controls.
- `10–12`: shared admin elements, health/calibration, operations/configuration.
- `13–14`: responsive rules and admin section components.
- `16`: personal panel.
- `17`: reduced motion and scoped workspace refinements.

The final scoped refinements are intentionally retained in their existing position
until their overrides can be folded into owning components with computed-style and
interaction checks. Do not append unrelated fixes here or delete selectors solely
because they are absent from static HTML searches: JavaScript and server templates
generate classes dynamically.

Production still serves `/static/style.css` from `admin.css` and derives its ETag
from the assembled content. Deployment and rollback need only the same bundle,
not Node.js or these source sections on the runtime host.
