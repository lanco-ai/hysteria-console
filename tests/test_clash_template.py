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


def test_google_uses_tcp_first_fallback_group():
    cfg = load_template()
    groups = {group["name"]: group for group in cfg["proxy-groups"]}

    google_group = groups["🌐 Google 优化"]
    assert google_group["type"] == "fallback"
    assert google_group["url"] == "https://www.gstatic.com/generate_204"
    assert google_group["timeout"] == 3000
    assert google_group["proxies"] == [
        "🇺🇸 美国 TCP (VLESS+REALITY)",
        "🇺🇸 美国 TCP 备用 (VLESS+REALITY)",
        "🇺🇸 美国 UDP (端口跳跃)",
        "🇺🇸 美国 UDP TUIC",
    ]
    assert "🌐 Google 优化" in groups["🚀 节点选择"]["proxies"]


def test_academic_access_group_defaults_to_direct():
    cfg = load_template()
    groups = {group["name"]: group for group in cfg["proxy-groups"]}

    academic_group = groups["📚 学术访问"]
    assert academic_group["type"] == "select"
    assert academic_group["proxies"] == [
        "DIRECT",
        "🇺🇸 美国 TCP (VLESS+REALITY)",
        "🇺🇸 美国 TCP 备用 (VLESS+REALITY)",
        "🇺🇸 美国 UDP (端口跳跃)",
        "🇺🇸 美国 UDP TUIC",
    ]
    assert "📚 学术访问" in groups["🚀 节点选择"]["proxies"]


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


def test_ipv6_dead_end_rules_precede_proxy_rules():
    cfg = load_template()
    rules = cfg["rules"]
    ipv6_rules = [
        "DOMAIN,ipv6.msftconnecttest.com,REJECT",
        "DOMAIN,ipv6.msftncsi.com,REJECT",
        "IP-CIDR6,::/0,REJECT,no-resolve",
    ]
    first_proxy_rule = rules.index("DOMAIN-SUFFIX,openai.com,🤖 GPT 优化")
    first_ruleset = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,"))

    # All three guards must precede proxy dispatch; their relative order is immaterial.
    assert set(rules[2:5]) == set(ipv6_rules)
    assert all(rules.index(rule) < first_proxy_rule for rule in ipv6_rules)
    assert all(rules.index(rule) < first_ruleset for rule in ipv6_rules)


def test_microsoft_store_xbox_rules_stay_direct_before_proxy_rules():
    cfg = load_template()
    rules = cfg["rules"]
    microsoft_rules = [
        "DOMAIN-SUFFIX,msftconnecttest.com,DIRECT",
        "DOMAIN-SUFFIX,msftncsi.com,DIRECT",
        "DOMAIN-SUFFIX,microsoft.com,DIRECT",
        "DOMAIN-SUFFIX,microsoftonline.com,DIRECT",
        "DOMAIN-SUFFIX,windows.com,DIRECT",
        "DOMAIN-SUFFIX,windowsupdate.com,DIRECT",
        "DOMAIN-SUFFIX,mp.microsoft.com,DIRECT",
        "DOMAIN-SUFFIX,xboxlive.com,DIRECT",
        "DOMAIN-SUFFIX,xboxservices.com,DIRECT",
        "DOMAIN-SUFFIX,gamepass.com,DIRECT",
        "DOMAIN-SUFFIX,playfabapi.com,DIRECT",
    ]
    first_proxy_rule = rules.index("DOMAIN-SUFFIX,openai.com,🤖 GPT 优化")
    first_ruleset = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,"))

    assert rules.index("DOMAIN,ipv6.msftconnecttest.com,REJECT") < rules.index(
        "DOMAIN-SUFFIX,msftconnecttest.com,DIRECT")
    assert rules.index("DOMAIN,ipv6.msftncsi.com,REJECT") < rules.index(
        "DOMAIN-SUFFIX,msftncsi.com,DIRECT")
    assert all(rule in rules for rule in microsoft_rules)
    assert all(rules.index(rule) < first_proxy_rule for rule in microsoft_rules)
    assert all(rules.index(rule) < first_ruleset for rule in microsoft_rules)


def test_microsoft_fake_ip_filters_are_present():
    cfg = load_template()
    filters = cfg["dns"]["fake-ip-filter"]

    for pattern in (
        "*.msftconnecttest.com",
        "*.msftncsi.com",
        "*.microsoft.com",
        "*.windowsupdate.com",
        "*.mp.microsoft.com",
        "*.xboxlive.com",
        "*.xboxservices.com",
    ):
        assert pattern in filters


def test_academic_rules_precede_external_rulesets():
    cfg = load_template()
    rules = cfg["rules"]
    academic_rules = [
        "DOMAIN-SUFFIX,sciencedirect.com,📚 学术访问",
        "DOMAIN-SUFFIX,sciencedirectassets.com,📚 学术访问",
        "DOMAIN-SUFFIX,els-cdn.com,📚 学术访问",
        "DOMAIN-SUFFIX,elsevier.com,📚 学术访问",
        "DOMAIN-SUFFIX,scopus.com,📚 学术访问",
        "DOMAIN-SUFFIX,springer.com,📚 学术访问",
        "DOMAIN-SUFFIX,nature.com,📚 学术访问",
        "DOMAIN-SUFFIX,wiley.com,📚 学术访问",
        "DOMAIN-SUFFIX,ieee.org,📚 学术访问",
    ]
    first_proxy_rule = rules.index("DOMAIN-SUFFIX,openai.com,🤖 GPT 优化")
    first_ruleset = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,"))

    assert all(rule in rules for rule in academic_rules)
    assert all(rules.index(rule) < first_proxy_rule for rule in academic_rules)
    assert all(rules.index(rule) < first_ruleset for rule in academic_rules)


def test_academic_fake_ip_filters_are_present():
    cfg = load_template()
    filters = cfg["dns"]["fake-ip-filter"]

    for pattern in (
        "*.sciencedirect.com",
        "*.sciencedirectassets.com",
        "*.els-cdn.com",
        "*.elsevier.com",
        "*.scopus.com",
        "*.springer.com",
        "*.nature.com",
        "*.wiley.com",
    ):
        assert pattern in filters


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


def test_overleaf_rules_precede_external_rulesets():
    cfg = load_template()
    rules = cfg["rules"]
    overleaf_rules = [
        "DOMAIN-SUFFIX,overleaf.com,🚀 节点选择",
        "DOMAIN-SUFFIX,overleafusercontent.com,🚀 节点选择",
        "DOMAIN-SUFFIX,sharelatex.com,🚀 节点选择",
    ]
    # Telegram's dedicated IP set cannot match Overleaf domains. The relevant
    # boundary is the general ruleset chain, beginning with reject.
    first_ruleset = rules.index('RULE-SET,reject,REJECT')

    assert all(rule in rules for rule in overleaf_rules)
    assert all(rules.index(rule) < first_ruleset for rule in overleaf_rules)


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
    assert "DOMAIN-SUFFIX,recaptcha.net,🌐 Google 优化" in rules
    assert "DOMAIN-SUFFIX,doubleclick.net,🌐 Google 优化" in rules
    assert "DOMAIN-SUFFIX,firebaseapp.com,🌐 Google 优化" in rules
    google_direct_rules = [
        rule for rule in rules
        if "google" in rule.lower() and rule.endswith(",DIRECT")
    ]
    assert google_direct_rules == ["DOMAIN-SUFFIX,googleapis.cn,DIRECT"]


def test_google_play_required_domains_use_google_group():
    cfg = load_template()
    rules = cfg["rules"]
    first_general_rule = next(
        i for i, rule in enumerate(rules)
        if rule.startswith(("RULE-SET,", "GEOIP,", "MATCH,"))
    )

    for domain in (
        "android.com",
        "google.com",
        "googleapis.com",
        "gstatic.com",
        "googleusercontent.com",
        "ggpht.com",
        "gvt1.com",
        "gvt2.com",
        "gvt3.com",
    ):
        rule = f"DOMAIN-SUFFIX,{domain},🌐 Google 优化"
        assert rule in rules
        assert rules.index(rule) < first_general_rule


def test_google_play_store_and_mainland_download_chain_use_split_routes():
    cfg = load_template()
    rules = cfg["rules"]

    def explicit_domain_action(host):
        for rule in rules:
            parts = rule.split(",")
            if parts[0] == "RULE-SET":
                return None
            if parts[0] == "DOMAIN" and host == parts[1]:
                return parts[2]
            if parts[0] == "DOMAIN-SUFFIX" and (
                host == parts[1] or host.endswith(f".{parts[1]}")
            ):
                return parts[2]
        return None

    assert explicit_domain_action("play.google.com") == "🌐 Google 优化"
    assert explicit_domain_action("android.clients.google.com") == "🌐 Google 优化"
    assert explicit_domain_action("services.googleapis.cn") == "DIRECT"
    assert explicit_domain_action(
        "rr1---sn-ni57rn7d.xn--ngstr-lra8j.com"
    ) == "DIRECT"


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
    direct_index = list(policy).index("rule-set:direct")

    for domain in ("+.github.com", "+.githubusercontent.com", "+.ghcr.io"):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://dns.google/dns-query",
        ]
        assert list(policy).index(domain) < direct_index


def test_gpt_dns_uses_overseas_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]
    direct_index = list(policy).index("rule-set:direct")

    for domain in ("+.openai.com", "+.chatgpt.com", "+.oaistatic.com", "+.oaiusercontent.com"):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://dns.google/dns-query",
        ]
        assert list(policy).index(domain) < direct_index


def test_google_play_dns_uses_overseas_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]
    direct_index = list(policy).index("rule-set:direct")

    for domain in (
        "+.android.com",
        "+.google.com",
        "+.googleapis.com",
        "+.gstatic.com",
        "+.googleusercontent.com",
        "+.ggpht.com",
        "+.gvt1.com",
        "+.gvt2.com",
        "+.gvt3.com",
    ):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://dns.google/dns-query",
        ]
        assert list(policy).index(domain) < direct_index


def test_google_play_mainland_download_dns_uses_domestic_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]

    for domain in ("+.googleapis.cn", "+.xn--ngstr-lra8j.com"):
        assert policy[domain] == [
            "https://doh.pub/dns-query",
            "https://dns.alidns.com/dns-query",
        ]


def test_direct_dns_policy_is_the_last_fallback():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]

    assert list(policy)[-1] == "rule-set:direct"


def test_telegram_dns_uses_overseas_resolvers():
    cfg = load_template()
    policy = cfg["dns"]["nameserver-policy"]
    direct_index = list(policy).index("rule-set:direct")

    for domain in ("+.telegram.org", "+.telegram.me", "+.t.me", "+.telegra.ph"):
        assert policy[domain] == [
            "https://1.1.1.1/dns-query",
            "https://dns.google/dns-query",
        ]
        assert list(policy).index(domain) < direct_index


def test_tcp_vless_nodes_do_not_tunnel_udp():
    cfg = load_template()
    proxies = {proxy["name"]: proxy for proxy in cfg["proxies"]}

    assert proxies["🇺🇸 美国 TCP (VLESS+REALITY)"]["udp"] is False
    assert proxies["🇺🇸 美国 TCP 备用 (VLESS+REALITY)"]["udp"] is False
