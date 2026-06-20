from pathlib import Path

import yaml


TEMPLATE = Path(__file__).resolve().parents[1] / "hysteria" / "clash-default.yaml.tpl"


def load_template():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def test_github_uses_dedicated_url_test_group():
    cfg = load_template()
    groups = {group["name"]: group for group in cfg["proxy-groups"]}

    github_group = groups["⚡ GitHub 加速"]
    assert github_group["type"] == "url-test"
    assert github_group["url"].startswith("https://github.com/")
    assert "⚡ GitHub 加速" in groups["🚀 节点选择"]["proxies"]


def test_gpt_uses_dedicated_url_test_group():
    cfg = load_template()
    groups = {group["name"]: group for group in cfg["proxy-groups"]}

    gpt_group = groups["🤖 GPT 优化"]
    assert gpt_group["type"] == "url-test"
    assert gpt_group["url"] == "https://chatgpt.com/cdn-cgi/trace"
    assert gpt_group["proxies"][:2] == [
        "🇺🇸 美国 UDP (端口跳跃)",
        "🇺🇸 美国 UDP TUIC",
    ]
    assert "🇺🇸 美国 TCP (VLESS+REALITY)" in gpt_group["proxies"]
    assert "🤖 GPT 优化" in groups["🚀 节点选择"]["proxies"]


def test_google_uses_dedicated_url_test_group():
    cfg = load_template()
    groups = {group["name"]: group for group in cfg["proxy-groups"]}

    google_group = groups["🌐 Google 优化"]
    assert google_group["type"] == "url-test"
    assert google_group["url"] == "https://www.gstatic.com/generate_204"
    assert google_group["proxies"][:2] == [
        "🇺🇸 美国 UDP (端口跳跃)",
        "🇺🇸 美国 UDP TUIC",
    ]
    assert "🇺🇸 美国 TCP (VLESS+REALITY)" in google_group["proxies"]
    assert "🌐 Google 优化" in groups["🚀 节点选择"]["proxies"]


def test_telegram_uses_dedicated_url_test_group():
    cfg = load_template()
    groups = {group["name"]: group for group in cfg["proxy-groups"]}

    telegram_group = groups["✈️ Telegram 优化"]
    assert telegram_group["type"] == "url-test"
    assert telegram_group["url"] == "https://telegram.org/img/website_icon.svg"
    assert telegram_group["proxies"][:2] == [
        "🇺🇸 美国 UDP (端口跳跃)",
        "🇺🇸 美国 UDP TUIC",
    ]
    assert "🇺🇸 美国 TCP (VLESS+REALITY)" in telegram_group["proxies"]
    assert "✈️ Telegram 优化" in groups["🚀 节点选择"]["proxies"]


def test_direct_ip_bypass_rule_stays_first():
    cfg = load_template()
    rules = cfg["rules"]
    direct_rules = [
        "IP-CIDR,47.245.53.96/32,DIRECT,no-resolve",
        "IP-CIDR,192.238.178.243/32,DIRECT,no-resolve",
    ]

    assert rules[:2] == direct_rules
    openai_index = rules.index("DOMAIN-SUFFIX,openai.com,🤖 GPT 优化")
    assert all(rules.index(rule) < openai_index for rule in direct_rules)


def test_github_rules_precede_external_rulesets():
    cfg = load_template()
    rules = cfg["rules"]
    github_rule_indexes = [
        i for i, rule in enumerate(rules)
        if "github" in rule.lower() or "ghcr.io" in rule.lower()
    ]
    first_ruleset = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,"))

    assert github_rule_indexes
    assert max(github_rule_indexes) < first_ruleset
    assert "DOMAIN-SUFFIX,githubusercontent.com,⚡ GitHub 加速" in rules
    assert not any("github" in rule.lower() and rule.endswith(",DIRECT") for rule in rules)


def test_gpt_rules_precede_external_rulesets():
    cfg = load_template()
    rules = cfg["rules"]
    gpt_rule_indexes = [
        i for i, rule in enumerate(rules)
        if any(token in rule.lower() for token in ("openai", "chatgpt", "oaistatic", "oaiusercontent"))
    ]
    first_ruleset = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,"))

    assert gpt_rule_indexes
    assert max(gpt_rule_indexes) < first_ruleset
    assert "DOMAIN-SUFFIX,openai.com,🤖 GPT 优化" in rules
    assert "DOMAIN-SUFFIX,chatgpt.com,🤖 GPT 优化" in rules
    assert "DOMAIN,challenges.cloudflare.com,🤖 GPT 优化" in rules
    assert not any("openai" in rule.lower() and rule.endswith(",DIRECT") for rule in rules)


def test_google_rules_precede_external_rulesets():
    cfg = load_template()
    rules = cfg["rules"]
    google_rule_indexes = [
        i for i, rule in enumerate(rules)
        if any(token in rule.lower() for token in ("google", "gmail", "gstatic", "youtube"))
    ]
    first_ruleset = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,"))

    assert google_rule_indexes
    assert max(google_rule_indexes) < first_ruleset
    assert "DOMAIN-SUFFIX,gmail.com,🌐 Google 优化" in rules
    assert "DOMAIN-SUFFIX,google.com,🌐 Google 优化" in rules
    assert "DOMAIN-SUFFIX,gstatic.com,🌐 Google 优化" in rules
    assert not any("google" in rule.lower() and rule.endswith(",DIRECT") for rule in rules)


def test_telegram_rules_precede_general_rulesets():
    cfg = load_template()
    rules = cfg["rules"]
    telegram_domain_indexes = [
        i for i, rule in enumerate(rules)
        if any(token in rule.lower() for token in ("telegram", "t.me", "telegra.ph", "tdesktop"))
        and rule.startswith("DOMAIN")
    ]
    first_ruleset = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,"))

    assert telegram_domain_indexes
    assert max(telegram_domain_indexes) < first_ruleset
    assert "DOMAIN-SUFFIX,telegram.org,✈️ Telegram 优化" in rules
    assert "DOMAIN-SUFFIX,t.me,✈️ Telegram 优化" in rules
    assert "RULE-SET,telegramcidr,✈️ Telegram 优化,no-resolve" in rules
    assert not any("telegram" in rule.lower() and rule.endswith(",DIRECT") for rule in rules)


def test_github_dns_uses_overseas_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]

    for domain in ("+.github.com", "+.githubusercontent.com", "+.ghcr.io"):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://8.8.8.8/dns-query",
        ]


def test_gpt_dns_uses_overseas_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]

    for domain in ("+.openai.com", "+.chatgpt.com", "+.oaistatic.com", "+.oaiusercontent.com"):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://8.8.8.8/dns-query",
        ]


def test_google_dns_uses_overseas_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]

    for domain in ("+.google.com", "+.gmail.com", "+.googleapis.com", "+.gstatic.com"):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://8.8.8.8/dns-query",
        ]


def test_telegram_dns_uses_overseas_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]

    for domain in ("+.telegram.org", "+.telegram.me", "+.t.me", "+.telegra.ph"):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://8.8.8.8/dns-query",
        ]


def test_tcp_vless_nodes_do_not_tunnel_udp():
    cfg = load_template()
    proxies = {proxy["name"]: proxy for proxy in cfg["proxies"]}

    assert proxies["🇺🇸 美国 TCP (VLESS+REALITY)"]["udp"] is False
    assert proxies["🇺🇸 美国 TCP 备用 (VLESS+REALITY)"]["udp"] is False
