"""Aggregate TUIC tunnel traffic metering.

TUIC v5 does not expose the user-level stats API that Hysteria/Xray provide in
this stack, so this module tracks only tunnel bytes on the TUIC listen port.
The counters are protocol-level and must not be used for per-user quota math.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import state_store
import tuic_config


NFT_BIN = os.environ.get("HY2_NFT_BIN", "nft")
NFT_FAMILY = "inet"
NFT_TABLE = "hy2_meter"
NFT_INPUT_CHAIN = "tuic_meter_input"
NFT_OUTPUT_CHAIN = "tuic_meter_output"
STATE_FILE = "/root/hysteria/state/tuic_meter_state.json"
DEFAULT_PORT = 9443


def parse_listen_port(value, *, default=DEFAULT_PORT):
    text = str(value or "").strip()
    if text.isdigit():
        port = int(text)
        return port if 1 <= port <= 65535 else default
    match = re.search(r":(\d+)\s*$", text)
    if not match:
        return default
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else default


def load_listen_port(*, config_file=None):
    path = Path(config_file) if config_file else tuic_config.CONFIG_FILE
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return parse_listen_port((cfg or {}).get("server"), default=DEFAULT_PORT)


def _nft(args, *, check=True):
    return subprocess.run(
        [NFT_BIN, *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=3,
    )


def _nft_json(args):
    out = subprocess.check_output(
        [NFT_BIN, "-j", *args],
        stderr=subprocess.DEVNULL,
        timeout=3,
    )
    return json.loads(out.decode("utf-8"))


def _counter_comments(port):
    return {
        "rx": f"hy2_tuic_rx_{int(port)}",
        "tx": f"hy2_tuic_tx_{int(port)}",
    }


def _rule_comments(ruleset):
    found = set()
    for item in (ruleset or {}).get("nftables", []):
        rule = item.get("rule")
        if not rule or rule.get("family") != NFT_FAMILY or rule.get("table") != NFT_TABLE:
            continue
        for expr in rule.get("expr") or []:
            comment = expr.get("comment")
            if comment:
                found.add(comment)
    return found


def _ensure_chain(name, hook):
    if _nft(["list", "chain", NFT_FAMILY, NFT_TABLE, name], check=False).returncode == 0:
        return
    _nft([
        "add", "chain", NFT_FAMILY, NFT_TABLE, name,
        "{", "type", "filter", "hook", hook, "priority", "filter", ";",
        "policy", "accept", ";", "}",
    ])


def ensure_nft_counters(port):
    """Create non-verdict nft counter rules for TUIC client tunnel traffic."""
    _nft(["add", "table", NFT_FAMILY, NFT_TABLE], check=False)
    _ensure_chain(NFT_INPUT_CHAIN, "input")
    _ensure_chain(NFT_OUTPUT_CHAIN, "output")
    ruleset = _nft_json(["list", "ruleset"])
    comments = _rule_comments(ruleset)
    wanted = _counter_comments(port)
    if wanted["rx"] not in comments:
        _nft([
            "add", "rule", NFT_FAMILY, NFT_TABLE, NFT_INPUT_CHAIN,
            "udp", "dport", str(int(port)), "counter", "comment", wanted["rx"],
        ])
    if wanted["tx"] not in comments:
        _nft([
            "add", "rule", NFT_FAMILY, NFT_TABLE, NFT_OUTPUT_CHAIN,
            "udp", "sport", str(int(port)), "counter", "comment", wanted["tx"],
        ])


def extract_counters(ruleset, port):
    comments = _counter_comments(port)
    totals = {"rx": 0, "tx": 0}
    for item in (ruleset or {}).get("nftables", []):
        rule = item.get("rule")
        if not rule or rule.get("family") != NFT_FAMILY or rule.get("table") != NFT_TABLE:
            continue
        exprs = rule.get("expr") or []
        rule_comments = {
            expr.get("comment")
            for expr in exprs
            if expr.get("comment")
        }
        counter = next((expr.get("counter") for expr in exprs if expr.get("counter")), None)
        if not counter:
            continue
        bytes_n = int(counter.get("bytes", 0) or 0)
        if comments["rx"] in rule_comments:
            totals["rx"] = max(totals["rx"], bytes_n)
        if comments["tx"] in rule_comments:
            totals["tx"] = max(totals["tx"], bytes_n)
    totals["total"] = totals["rx"] + totals["tx"]
    return totals


def read_nft_counters(port):
    ensure_nft_counters(port)
    return extract_counters(_nft_json(["list", "ruleset"]), port)


def _delta(current, previous):
    if previous is None:
        return 0
    current = int(current or 0)
    previous = int(previous or 0)
    if current >= previous:
        return current - previous
    # Counter rules were recreated/reset; keep the post-reset bytes and avoid
    # a negative delta.
    return current


def counter_delta(current, *, port, state_file=STATE_FILE):
    state = state_store.load_json(state_file, {}) or {}
    try:
        state_port = int(state.get("port") or 0)
    except (TypeError, ValueError):
        state_port = 0
    if state_port != int(port):
        state = {}
    delta = {
        "rx": _delta(current.get("rx"), state.get("rx")),
        "tx": _delta(current.get("tx"), state.get("tx")),
    }
    delta["total"] = delta["rx"] + delta["tx"]
    state_store.save_json(state_file, {
        "port": int(port),
        "rx": int(current.get("rx", 0) or 0),
        "tx": int(current.get("tx", 0) or 0),
        "total": int(current.get("total", 0) or 0),
    })
    return delta


def _default_lock_file(state_file):
    return str(Path(state_file).with_suffix(".lock"))


def get_tuic_traffic(*, config_file=None, state_file=STATE_FILE, lock_file=None):
    """Return TUIC aggregate delta as {'tx', 'rx', 'total'}.

    First run after enabling the counter stores a baseline and returns zero so
    old accumulated firewall bytes do not appear as a sudden traffic spike.
    """
    try:
        with state_store.file_lock(lock_file or _default_lock_file(state_file)):
            port = load_listen_port(config_file=config_file)
            current = read_nft_counters(port)
            return counter_delta(current, port=port, state_file=state_file)
    except Exception as e:
        print(f"tuic meter skipped: {e}", file=sys.stderr)
        return {}
