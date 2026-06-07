"""Subscription profile and Clash YAML rendering helpers."""
import logging
import re
from dataclasses import dataclass


log = logging.getLogger(__name__)

NODE_GROUP = '🚀 节点选择'
AUTO_GROUP = '🔄 自动选择'
GITHUB_GROUP = '⚡ GitHub 加速'
GPT_GROUP = '🤖 GPT 优化'
GOOGLE_GROUP = '🌐 Google 优化'
TELEGRAM_GROUP = '✈️ Telegram 优化'
HY2_UDP_PROXY = '🇺🇸 美国 UDP (端口跳跃)'
TUIC_UDP_PROXY = '🇺🇸 美国 UDP TUIC'
VLESS_TCP_PROXY = '🇺🇸 美国 TCP (VLESS+REALITY)'
VLESS_BACKUP_PROXY = '🇺🇸 美国 TCP 备用 (VLESS+REALITY)'

SUBSCRIPTION_PROFILES = {
    'default': {
        'label': '默认',
        'desc': '保持后台模板策略',
    },
    'game': {
        'label': '游戏',
        'desc': '优先 UDP，低延迟测试更激进',
    },
    'work': {
        'label': '办公',
        'desc': '优先 TCP/备用线路，稳定性优先',
    },
    'lowdata': {
        'label': '省流',
        'desc': '未知流量直连，只代理规则命中的域名',
    },
    'safe': {
        'label': '全代理',
        'desc': '除局域网/私有地址外尽量走代理',
    },
}
SUBSCRIPTION_PROFILE_ORDER = ('default', 'game', 'work', 'lowdata', 'safe')
_PROFILE_ALIASES = {
    '': 'default',
    'normal': 'default',
    'auto': 'default',
    'office': 'work',
    'stable': 'work',
    'low-data': 'lowdata',
    'low_data': 'lowdata',
    'save': 'lowdata',
    'global': 'safe',
    'proxy': 'safe',
    'full': 'safe',
}


@dataclass(frozen=True)
class SubscriptionProfileContext:
    template_file: object
    users_file: object
    load_json: object


def normalize_subscription_profile(raw):
    key = str(raw or '').strip().lower()
    key = _PROFILE_ALIASES.get(key, key)
    if key not in SUBSCRIPTION_PROFILES:
        return 'default'
    return key


def _proxy_names(cfg):
    return {
        str(proxy.get('name'))
        for proxy in (cfg.get('proxies') or [])
        if isinstance(proxy, dict) and proxy.get('name')
    }


def _proxy_group_map(cfg):
    return {
        str(group.get('name')): group
        for group in (cfg.get('proxy-groups') or [])
        if isinstance(group, dict) and group.get('name')
    }


def _dedupe(seq):
    out = []
    seen = set()
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _set_group_proxies(cfg, group_name, preferred, *,
                       allow_group_refs=False, allow_direct=False,
                       group_type=None, interval=None, timeout=None,
                       tolerance=None):
    groups = _proxy_group_map(cfg)
    group = groups.get(group_name)
    if not group:
        return
    allowed = set(_proxy_names(cfg))
    if allow_group_refs:
        allowed.update(name for name in groups if name != group_name)
    if allow_direct:
        allowed.add('DIRECT')

    current = [p for p in (group.get('proxies') or []) if p in allowed]
    ordered = [p for p in preferred if p in allowed and p != group_name]
    group['proxies'] = _dedupe(ordered + current)
    if group_type:
        group['type'] = group_type
    if interval is not None:
        group['interval'] = interval
    if timeout is not None:
        group['timeout'] = timeout
    if tolerance is not None:
        group['tolerance'] = tolerance


def _prepend_unique_rules(cfg, new_rules):
    rules = list(cfg.get('rules') or [])
    existing = [rule for rule in rules if rule not in new_rules]
    cfg['rules'] = new_rules + existing


def _replace_match_rule(cfg, action):
    rules = []
    replaced = False
    for rule in (cfg.get('rules') or []):
        if isinstance(rule, str) and rule.startswith('MATCH,'):
            rules.append(f'MATCH,{action}')
            replaced = True
        else:
            rules.append(rule)
    if not replaced:
        rules.append(f'MATCH,{action}')
    cfg['rules'] = rules


def _rewrite_rule_action(rule, action):
    parts = str(rule).split(',')
    if not parts:
        return rule
    if parts[0] == 'MATCH':
        return f'MATCH,{action}'
    if len(parts) >= 3:
        parts[2] = action
        return ','.join(parts)
    return rule


def _apply_game_profile(cfg):
    _set_group_proxies(
        cfg, NODE_GROUP,
        [HY2_UDP_PROXY, TUIC_UDP_PROXY, GPT_GROUP, GOOGLE_GROUP,
         TELEGRAM_GROUP, AUTO_GROUP, VLESS_TCP_PROXY, VLESS_BACKUP_PROXY,
         GITHUB_GROUP, 'DIRECT'],
        allow_group_refs=True, allow_direct=True,
    )
    _set_group_proxies(
        cfg, AUTO_GROUP,
        [HY2_UDP_PROXY, TUIC_UDP_PROXY, VLESS_TCP_PROXY, VLESS_BACKUP_PROXY],
        group_type='url-test', interval=20, timeout=2500, tolerance=50,
    )
    _prepend_unique_rules(cfg, [
        f'DOMAIN-SUFFIX,steamcommunity.com,{NODE_GROUP}',
        f'DOMAIN-SUFFIX,epicgames.com,{NODE_GROUP}',
        f'DOMAIN-SUFFIX,epicgames.dev,{NODE_GROUP}',
        f'DOMAIN-SUFFIX,riotgames.com,{NODE_GROUP}',
        'DOMAIN-SUFFIX,steamcontent.com,DIRECT',
        'DOMAIN-SUFFIX,steamserver.net,DIRECT',
    ])


def _apply_work_profile(cfg):
    _set_group_proxies(
        cfg, NODE_GROUP,
        [GITHUB_GROUP, GPT_GROUP, GOOGLE_GROUP, TELEGRAM_GROUP, AUTO_GROUP,
         VLESS_TCP_PROXY, VLESS_BACKUP_PROXY, HY2_UDP_PROXY, TUIC_UDP_PROXY,
         'DIRECT'],
        allow_group_refs=True, allow_direct=True,
    )
    _set_group_proxies(
        cfg, AUTO_GROUP,
        [VLESS_TCP_PROXY, VLESS_BACKUP_PROXY, HY2_UDP_PROXY, TUIC_UDP_PROXY],
        group_type='fallback', interval=60, timeout=6000,
    )
    _prepend_unique_rules(cfg, [
        f'DOMAIN-SUFFIX,slack.com,{NODE_GROUP}',
        f'DOMAIN-SUFFIX,notion.so,{NODE_GROUP}',
        f'DOMAIN-SUFFIX,zoom.us,{NODE_GROUP}',
        f'DOMAIN-SUFFIX,linear.app,{NODE_GROUP}',
    ])


def _apply_lowdata_profile(cfg):
    cfg['log-level'] = 'warning'
    _set_group_proxies(
        cfg, NODE_GROUP,
        ['DIRECT', GPT_GROUP, GOOGLE_GROUP, TELEGRAM_GROUP, AUTO_GROUP,
         VLESS_TCP_PROXY, VLESS_BACKUP_PROXY, HY2_UDP_PROXY, TUIC_UDP_PROXY,
         GITHUB_GROUP],
        allow_group_refs=True, allow_direct=True,
    )
    _set_group_proxies(
        cfg, AUTO_GROUP,
        [VLESS_TCP_PROXY, VLESS_BACKUP_PROXY, HY2_UDP_PROXY, TUIC_UDP_PROXY],
        group_type='fallback', interval=90, timeout=6000,
    )
    _replace_match_rule(cfg, 'DIRECT')


def _apply_safe_profile(cfg):
    _set_group_proxies(
        cfg, NODE_GROUP,
        [GPT_GROUP, GOOGLE_GROUP, TELEGRAM_GROUP, AUTO_GROUP, VLESS_TCP_PROXY,
         VLESS_BACKUP_PROXY, HY2_UDP_PROXY, TUIC_UDP_PROXY, 'DIRECT'],
        allow_group_refs=True, allow_direct=True,
    )
    _set_group_proxies(
        cfg, AUTO_GROUP,
        [VLESS_TCP_PROXY, VLESS_BACKUP_PROXY, HY2_UDP_PROXY, TUIC_UDP_PROXY],
        group_type='fallback', interval=45, timeout=5000,
    )
    keep_direct = (
        'RULE-SET,private,DIRECT',
        'RULE-SET,lancidr,DIRECT',
        'GEOIP,LAN,DIRECT',
    )
    rewritten = []
    for rule in (cfg.get('rules') or []):
        if not isinstance(rule, str):
            rewritten.append(rule)
        elif rule.startswith('RULE-SET,reject,') or rule.startswith(keep_direct):
            rewritten.append(rule)
        elif rule.startswith('MATCH,'):
            rewritten.append(f'MATCH,{NODE_GROUP}')
        elif ',DIRECT' in rule:
            rewritten.append(_rewrite_rule_action(rule, NODE_GROUP))
        else:
            rewritten.append(rule)
    cfg['rules'] = rewritten
    _replace_match_rule(cfg, NODE_GROUP)


def apply_subscription_profile(cfg, profile):
    profile = normalize_subscription_profile(profile)
    if profile == 'default':
        return cfg
    appliers = {
        'game': _apply_game_profile,
        'work': _apply_work_profile,
        'lowdata': _apply_lowdata_profile,
        'safe': _apply_safe_profile,
    }
    appliers[profile](cfg)
    return cfg


def _dump_yaml(data):
    import yaml
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def render_profile_yaml(text, profile):
    profile = normalize_subscription_profile(profile)
    if profile == 'default':
        return text
    try:
        import yaml
        data = yaml.safe_load(text) or {}
        apply_subscription_profile(data, profile)
        return _dump_yaml(data)
    except Exception:
        log.exception('failed to render subscription profile %s', profile)
        return text


def build_yaml(ctx, username, auth_secret, profile='default'):
    if not ctx.template_file.exists():
        return ''
    text = ctx.template_file.read_text(encoding='utf-8')
    text = re.sub(
        r'(?m)^(\s*password:\s*).*$',
        lambda match: f'{match.group(1)}{username}:{auth_secret}',
        text,
        count=1,
    )
    users = ctx.load_json(ctx.users_file, {})
    vless_uuid = str((users.get(username) or {}).get('vless_uuid') or '').strip()
    if vless_uuid:
        text = re.sub(
            r'(?m)^(\s*uuid:\s*).*$',
            lambda match: f'{match.group(1)}{vless_uuid}',
            text,
        )
        text = re.sub(
            r'(?m)^(\s*password:\s*)TUIC_PASSWORD_PLACEHOLDER\s*$',
            lambda match: f'{match.group(1)}{username}:{auth_secret}',
            text,
        )
    return render_profile_yaml(text, profile)
