"""Static regressions for admin_poll.js / home.js / admin.css fixes.

Style follows test_static_js.py: plain source assertions that lock in the
invariants the runtime behaviour depends on.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _presentation_source():
    # Markup and external shell interactions are separate source owners.
    return '\n'.join(_read('hysteria/' + name) for name in (
        'subscription_service.py', 'console_shell_views.py',
        'admin_views.py', 'operations_views.py',
        'static/shell.js', 'static/shell-preferences.js',
    ))


def test_patch_user_row_syncs_revision_cache_not_just_dom():
    """tick() reads row.revision while the DOM carries tr.dataset.revision.
    Updating only the DOM side makes the next poll see a phantom change and
    force needsReload after every AJAX mutation (P0)."""
    js = _read("hysteria/admin_poll.js")
    assert "function setRowRevision(row, revision)" in js
    assert "row.revision = rev;" in js
    # The revision branch must compare against the cached value and write
    # through the helper — never touch dataset.revision on its own.
    branch = re.search(
        r"// --- revision sync.*?\n(.*?)\n\n", js, re.DOTALL,
    ).group(1)
    assert "setRowRevision(row, u.revision)" in branch
    assert "row.tr.dataset.revision = u.revision" not in branch


def test_stat_dot_position_lives_in_css_not_runtime_js():
    css = _read("hysteria/admin.css")
    home = _read("hysteria/static/home.js")
    for selector in (".stat-dot {",):
        block = re.search(
            re.escape(selector) + r"(.*?)\n\}", css, re.DOTALL,
        ).group(1)
        assert "position: relative" in block, selector
    # The runtime getComputedStyle patches are gone.
    assert "getComputedStyle(dot)" not in home
    assert 'querySelectorAll(".stat-dot")' not in home


def test_delete_user_deducts_header_total_locally():
    js = _read("hysteria/admin_poll.js")
    assert "var removedUsed = removeUserRow(name);" in js
    assert "lastTotal - removedUsed" in js
    # removeUserRow returns the last known usage for that deduction.
    assert "return removedUsed;" in js


def test_stale_overview_response_dropped_by_mutation_epoch():
    """A mutation overlapping an in-flight overview fetch must make tick()
    drop the stale response instead of comparing pre-mutation revisions
    and forcing needsReload."""
    js = _read("hysteria/admin_poll.js")
    assert "var mutationEpoch = 0;" in js
    # tick tags the fetch at send time...
    assert "var tickEpoch = mutationEpoch;" in js
    # ...and drops the response on mismatch before touching rows or
    # setting needsReload.
    drop = re.search(
        r"if \(tickEpoch !== mutationEpoch\) \{(.*?)\}", js, re.DOTALL,
    ).group(1)
    assert "return;" in drop
    # The drop path must not assign needsReload or patch any row.
    assert "needsReload = true" not in drop
    assert "patchUserRow" not in drop
    # The epoch moves on both mutation start and finish.
    assert js.count("mutationEpoch++") >= 2


def test_delete_never_restores_detached_controls():
    """Once removeUserRow() ran, neither the success nor the failure path
    may touch the detached row's buttons."""
    js = _read("hysteria/admin_poll.js")
    assert "row.__detached = true;" in js
    catch = re.search(
        r"\.catch\(function \(err\) \{(.*?)\n      \}\);", js, re.DOTALL,
    ).group(1)
    assert "row.__detached" in catch
    # The unconditional restore is gone from the failure path.
    assert "if (!(row && row.__detached)) restoreButtons(true);" in catch


def test_sidebar_collapse_controls_exist_in_shell():
    src = _presentation_source()
    css = _read("hysteria/admin.css")
    assert 'id="sidebar-collapse"' in src
    assert 'hy2.sidebar' in src
    assert 'aria-pressed' in src
    assert '.sidebar.collapsed' in css
    assert 'inset 2px 0 0 var(--data)' in css
    assert 'id="total-used"' in src


def test_sidebar_collapse_animates_grid_track_after_first_paint():
    src = _presentation_source()
    css = _read("hysteria/admin.css")
    animated_app = re.search(
        r"\.app\.anim-ready \{(.*?)\n\}", css, re.DOTALL,
    )
    assert animated_app
    assert (
        "transition: grid-template-columns 220ms "
        "cubic-bezier(.16, 1, .3, 1);"
        in animated_app.group(1)
    )
    assert "transition: width 180ms ease" not in css
    assert ".sidebar.collapsed {\n  width: 64px;\n}" not in css

    reduced_motion = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".app.anim-ready { transition: none; }" in reduced_motion
    mobile = re.search(
        r"@media \(max-width: 880px\) \{"
        r"(?P<body>(?:[^{}]+|\{[^{}]*\})*)\}",
        css,
        re.DOTALL,
    )
    assert mobile
    assert ".app.anim-ready { transition: none; }" in mobile.group("body")
    assert "app.classList.add('anim-ready')" in src
    assert re.search(
        r"requestAnimationFrame\(function\(\) \{\s*"
        r"requestAnimationFrame\(function\(\) \{\s*"
        r"document\.documentElement\.classList\.remove\("
        r"'sidebar-pre-collapsed'\);\s*"
        r"if \(app\) app\.classList\.add\('anim-ready'\)",
        src,
    )


def test_sidebar_motion_override_is_explicit_desktop_only_preference():
    src = _presentation_source()
    css = _read("hysteria/admin.css")

    assert 'id="sidebar-motion-toggle"' in src
    assert "localStorage.getItem('hy2.sidebar-motion') === 'enabled'" in src
    assert "localStorage.setItem('hy2.sidebar-motion', 'enabled')" in src
    assert "localStorage.removeItem('hy2.sidebar-motion')" in src
    assert "classList.add('sidebar-motion-enabled')" in src
    assert "classList.toggle('sidebar-motion-enabled'" in src

    override = re.search(
        r"@media \(prefers-reduced-motion: reduce\) and "
        r"\(min-width: 881px\) \{(?P<body>.*?)\n\}",
        css,
        re.DOTALL,
    )
    assert override
    assert "html.sidebar-motion-enabled .app.anim-ready" in override.group("body")
    assert (
        "transition: grid-template-columns 220ms "
        "cubic-bezier(.16, 1, .3, 1) !important;"
        in override.group("body")
    )

    import subscription_service as ss
    settings = ss.render_settings("panel.test")
    assert 'id="sidebar-motion-toggle"' in settings
    assert "本浏览器强制显示侧边栏动画" in settings


def test_panel_copy_control_has_visible_secure_share_feedback():
    src = _presentation_source()
    js = _read("hysteria/admin_poll.js")

    assert '<span class="copy-label">复制专属面板</span>' in src
    assert '打开后地址栏会安全归一为 <code>/user/panel</code>' in src
    assert "label.textContent = '已复制';" in js
    assert "label.textContent = previousLabel;" in js


def test_hysteria_update_buttons_use_ajax_and_poll_background_status():
    js = _read("hysteria/admin_poll.js")
    updater = _read("hysteria/hysteria_update.py")

    assert 'data-action="hysteria-update-check"' in updater
    assert 'data-action="hysteria-update-apply"' in updater
    assert "'hysteria-update-check': true" in js
    assert "'hysteria-update-apply': true" in js
    assert "HYSTERIA_UPDATE_STATUS_URL" in js
    assert "function watchHysteriaUpdateStatus()" in js
    assert "HYSTERIA_UPDATE_POLL_MAX_MS = 120000" in js
    assert "watchHysteriaUpdateStatus();" in js
    for reason in (
        "update_busy",
        "update_check_failed",
        "update_schedule_failed",
    ):
        assert reason in js
