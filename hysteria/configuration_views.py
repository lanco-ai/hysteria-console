"""configuration views with explicit presentation dependencies."""

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import web_assets


@dataclass(frozen=True)
class Context:
    RULE_PACKS: dict
    RULE_PACK_ORDER: list
    TEMPLATE_FILE: Path
    TemplateConfigError: type[Exception]
    USERS_FILE: Path
    _ACTION_LABELS: dict
    _CONFIG_FLASH: dict
    _RULES_FLASH: dict
    _RULE_TYPE_LABELS: dict
    _parse_clash_rule: Callable[..., object]
    _template_revision_unlocked: Callable[..., object]
    load_json: Callable[..., object]
    load_template_config_snapshot: Callable[..., object]
    load_template_rules_snapshot: Callable[..., object]
    render_admin_shell: Callable[..., object]
    render_alert: Callable[..., object]
    render_prefixed_alert: Callable[..., object]
    template_lock: Callable[..., object]


def render_config_editor(ctx: Context, host, flash='', *, draft=None, expected_revision=None):
    alert = ctx.render_prefixed_alert(flash, ctx._CONFIG_FLASH)
    load_failed = False
    editor_error_attrs = ' aria-invalid="true" autofocus' if flash.startswith('err:') else ''

    if draft is not None:
        config_json = str(draft)
        template_revision = str(expected_revision or '')
        if not re.fullmatch(r'[0-9a-f]{64}', template_revision):
            template_revision = ''
    else:
        try:
            data, template_revision = ctx.load_template_config_snapshot()
            config_json = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            load_failed = True
            with ctx.template_lock():
                template_revision = ctx._template_revision_unlocked()
            try:
                config_json = ctx.TEMPLATE_FILE.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                config_json = ''
            if not flash:
                alert = ctx.render_alert(
                    '模板加载失败。为避免覆盖原配置，编辑与保存已锁定；请修复文件后重新加载。',
                    'err',
                )

    locked_attrs = ' readonly aria-readonly="true" data-load-failed="true"' if load_failed else ''
    disabled_attrs = ' disabled aria-disabled="true"' if load_failed else ''
    recovery_actions = (
        '<a class="btn btn-secondary" href="/admin/config">重新加载模板</a>'
        if load_failed
        else (
            '<a class="btn btn-secondary" href="/admin/config">打开最新版本（请先复制草稿）</a>'
            if flash.removeprefix('err:') == 'conflict'
            else ''
        )
    )
    content = f'''{alert}
<div class="admin-page">
  <section class="form-section">
    <div class="form-section-title">模板说明与影响范围</div>
    <div class="form-section-desc">编辑 JSON 格式的订阅模板，校验通过后转换为 YAML 保存。这是整份替换，不会合并保留遗漏的原内容。</div>
    <ul class="template-impact"><li>影响后续订阅：用户更新订阅并应用后，客户端才会使用新配置。</li><li>不修改 Hysteria、Xray 等代理服务的运行配置，也不会重启代理服务。</li><li>每个用户的密码和 UUID 由服务端自动注入。覆盖前请自行保留原模板副本。</li></ul>
    <div class="small">模板文件：<code>{html.escape(str(ctx.TEMPLATE_FILE))}</code></div>
  </section>

  <section class="code-panel">
    <form method="post" action="/admin/config/save" id="configForm"
          data-confirm="将覆盖整份订阅模板，不会合并旧内容；影响用户后续订阅，不修改代理服务运行配置。确认保存？">
      <div class="code-panel-header">
        <div class="code-panel-title">模板 JSON</div>
        <div class="code-panel-actions">
          <button class="btn btn-ghost btn-sm" type="button" id="cfgFormat"{disabled_attrs}>格式化 JSON</button>
          <button class="btn btn-ghost btn-sm" type="button" id="cfgCollapse"{disabled_attrs}>折叠/展开</button>
        </div>
      </div>
      <div class="code-panel-body">
        <input type="hidden" name="template_revision" value="{html.escape(template_revision, quote=True)}">
        <div class="field-help" id="configEditorHelp">Tab 插入两个空格；按 Esc 后再按 Tab 可移出编辑器，Shift+Tab 可直接返回上一个控件。</div>
        <div id="jsonError" class="json-error" role="alert" aria-live="assertive"></div>
        <textarea name="config_json" id="configEditor" class="code-area code-tall" aria-label="订阅模板 JSON"
                  aria-describedby="configEditorHelp jsonError" spellcheck="false"{editor_error_attrs}{locked_attrs}>{html.escape(config_json)}</textarea>
        <div class="row mt-md gap-md">
          <button class="btn btn-primary" type="submit"{disabled_attrs}>保存订阅模板</button>
          {recovery_actions}
        </div>
      </div>
    </form>
  </section>
</div>
{web_assets.script_tag('config-editor')}'''
    return ctx.render_admin_shell('config', '订阅模板配置', content, badge=host)


def render_rules(ctx: Context, host, flash='', *, raw_draft=None, expected_revision=None):
    try:
        rules, template_revision = ctx.load_template_rules_snapshot()
    except (ctx.TemplateConfigError, OSError, UnicodeError):
        content = f"""{
            ctx.render_alert(
                '模板加载失败。为避免误删或覆盖，所有规则修改已锁定；请修复模板后重试。',
                'err',
            )
        }
<div class="card">
  <h2 class="section-title mb-sm">路由规则暂不可编辑</h2>
  <p class="small mb-md">当前文件未被修改。可前往模板页查看保留的原始内容，或修复文件后重新加载。</p>
  <div class="row">
    <a class="btn secondary" href="/admin/rules">重新加载规则</a>
    <a class="btn ghost" href="/admin/config">查看模板恢复页</a>
  </div>
</div>"""
        return ctx.render_admin_shell(
            'rules',
            '订阅路由规则',
            content,
            badge='不可用',
        )
    users = ctx.load_json(ctx.USERS_FILE, {})
    alert = ctx.render_prefixed_alert(flash, ctx._RULES_FLASH)

    rows = ''
    for i, rule_str in enumerate(rules):
        rtype, val, action, extra = ctx._parse_clash_rule(rule_str)
        type_label = ctx._RULE_TYPE_LABELS.get(rtype, rtype)
        action_label = ctx._ACTION_LABELS.get(action, action)
        extra_tag = f' <span class="small">({html.escape(extra)})</span>' if extra else ''
        is_system = rtype in ('RULE-SET', 'GEOIP', 'MATCH')
        del_btn = ''
        if not is_system:
            del_btn = (
                f'<form method="post" action="/admin/rules/delete" class="inline-form-row" data-action="delete-rule">'
                f'<input type="hidden" name="index" value="{i}">'
                f'<input type="hidden" name="expected_rule" value="{html.escape(rule_str, quote=True)}">'
                f'<input type="hidden" name="template_revision" value="{template_revision}">'
                f'<button class="btn btn-danger btn-sm" type="submit">删除</button>'
                f'</form>'
            )
        tr_class = ' class="system-row"' if is_system else ''
        rows += (
            f'<tr{tr_class}><td>{i + 1}</td><td>{html.escape(type_label)}</td>'
            f'<td class="break">{html.escape(val)}</td>'
            f'<td>{html.escape(action_label)}{extra_tag}</td>'
            f'<td>{del_btn}</td></tr>'
        )

    rules_text = html.escape(str(raw_draft) if raw_draft is not None else '\n'.join(rules))
    submitted_revision = str(expected_revision or template_revision)
    if not re.fullmatch(r'[0-9a-f]{64}', submitted_revision):
        submitted_revision = ''
    pack_options = ''.join(
        f'<option value="{html.escape(key)}">{html.escape(ctx.RULE_PACKS[key]["label"])}'
        f' · {html.escape(ctx.RULE_PACKS[key]["desc"])}</option>'
        for key in ctx.RULE_PACK_ORDER
    )
    user_options = ''.join(
        f'<option value="{html.escape(uid)}">{html.escape(uid)}</option>' for uid in sorted(users)
    )

    content = f'''{alert}
<div class="admin-page">

  <!-- Page intro -->
  <div class="rules-intro">
    <div class="rules-intro-note">自定义规则优先级高于规则集，从上到下依次匹配。灰色行为内置规则集，不可删除。</div>
  </div>

  <!-- Layer 1: 操作区 — 双栏 -->
  <div class="rules-ops-grid">

    <!-- 规则包 -->
    <div class="op-panel">
      <div class="op-panel-title">规则包</div>
      <div class="op-panel-desc">应用规则包会修改所选范围的路由配置。全局模板影响所有用户；单个用户会写入 users.json 的个人 Clash 覆盖项。</div>
      <form method="post" action="/admin/rule-pack/apply" class="op-form"
            data-confirm="应用规则包会修改所选范围的路由配置，确认继续？">
        <input type="hidden" name="template_revision" value="{template_revision}">
        <div class="op-form-grid">
          <div class="field">
            <label for="rule-pack">规则包</label>
            <select id="rule-pack" name="pack" class="select">{pack_options}</select>
          </div>
          <div class="field">
            <label for="rule-pack-scope">应用范围</label>
            <select id="rule-pack-scope" name="scope" class="select">
              <option value="global">全局模板</option>
              <option value="user">单个用户</option>
            </select>
          </div>
          <div class="field">
            <label for="rule-pack-user">用户（选择"单个用户"时生效）</label>
            <select id="rule-pack-user" name="user" class="select" disabled>
              <option value="">选择用户</option>{user_options}
            </select>
          </div>
        </div>
        <div class="op-form-footer">
          <span class="op-footer-hint">应用后将更新所选范围</span>
          <button class="btn btn-secondary" type="submit">应用规则包</button>
        </div>
      </form>
    </div>

    <!-- 添加自定义规则 -->
    <div class="op-panel">
      <div class="op-panel-title">添加自定义规则</div>
      <form method="post" action="/admin/rules/add" class="op-form">
        <input type="hidden" name="template_revision" value="{template_revision}">
        <div class="op-form-grid">
          <div class="field">
            <label for="new-rule-type">规则类型</label>
            <select id="new-rule-type" name="rule_type" class="select">
              <option value="DOMAIN-SUFFIX">DOMAIN-SUFFIX（域名后缀）</option>
              <option value="DOMAIN-KEYWORD">DOMAIN-KEYWORD（域名关键词）</option>
              <option value="DOMAIN">DOMAIN（完整域名）</option>
              <option value="IP-CIDR">IP-CIDR（IP 段）</option>
            </select>
          </div>
          <div class="field">
            <label for="new-rule-pattern">匹配值</label>
            <input id="new-rule-pattern" name="pattern" required class="input" placeholder="example.com 或 10.0.0.0/8">
          </div>
          <div class="field">
            <label for="new-rule-action">动作</label>
            <select id="new-rule-action" name="action" class="select">
              <option value="DIRECT">直连 (DIRECT)</option>
              <option value="🚀 节点选择">代理 (🚀 节点选择)</option>
              <option value="REJECT">拦截 (REJECT)</option>
            </select>
          </div>
          <div class="field">
            <label for="new-rule-extra">附加选项</label>
            <select id="new-rule-extra" name="extra" class="select">
              <option value="">无</option>
              <option value="no-resolve">no-resolve（IP 规则跳过 DNS 解析）</option>
            </select>
          </div>
        </div>
        <div class="op-form-footer">
          <span class="op-footer-hint">将插入规则列表最前</span>
          <button class="btn btn-primary" type="submit">+ 添加规则</button>
        </div>
      </form>
    </div>

  </div><!-- /.rules-ops-grid -->

  <!-- Layer 2: 直接编辑全部规则 — 默认折叠 -->
  <details class="rules-raw-editor">
    <summary class="rules-raw-summary">
      <span>直接编辑全部规则</span>
      <span class="rules-raw-badge">高级操作</span>
    </summary>
    <div class="rules-raw-body">
      <div class="rules-raw-help">每行一条规则，格式：<code>TYPE,匹配值,动作</code>。保存后同步到所有订阅模板。</div>
      <form method="post" action="/admin/rules/raw" class="op-form"
            data-confirm="保存会替换全部共享路由规则，并影响所有用户订阅，确认继续？">
        <input type="hidden" name="template_revision" value="{html.escape(submitted_revision, quote=True)}">
        <div class="field">
          <label for="rules-raw" class="sr-only">全部路由规则</label>
          <textarea id="rules-raw" name="rules_raw" class="rules-raw-textarea">{rules_text}</textarea>
        </div>
        <div class="op-form-footer">
          <button class="btn btn-danger" type="submit">覆盖全部规则</button>
        </div>
      </form>
    </div>
  </details>

  <!-- Layer 3: 当前规则列表 — 结果查看区 -->
  <section class="admin-section">
    <div class="admin-section-header">
      <h2 class="admin-section-title">当前规则列表</h2>
      <div class="small">{len(rules)} 条</div>
    </div>
    <div class="data-table-wrap" tabindex="0" aria-label="路由规则，可横向滚动">
      <table class="data-table">
        <thead>
          <tr><th class="rule-index-column">#</th><th>类型</th><th>匹配</th><th>动作</th><th class="rule-actions-column">操作</th></tr>
        </thead>
        <tbody>{rows or '<tr><td colspan="5" class="empty">暂无规则</td></tr>'}</tbody>
      </table>
    </div>
  </section>

</div><!-- /.admin-page -->
{web_assets.script_tag('rules')}'''
    return ctx.render_admin_shell('rules', '订阅路由规则', content, badge=f'{len(rules)} 条')
