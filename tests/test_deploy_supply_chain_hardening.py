"""Focused regressions for deployment rendering and binary provenance.

The shell snippets run only against pytest temporary files.  They never invoke
the real deploy entry point, systemd, or paths under /root/hysteria.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy.sh"
RENDERER = ROOT / "scripts" / "hy2-render-template.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _render_env(**overrides: str) -> dict[str, str]:
    values = {
        "HY_API_SECRET": "api-value",
        "HY_OBFS_PASSWORD": "obfs-value",
        "HY_SERVER_HOST": "panel.example.test",
        "HY_DISPLAY_MULTIPLIER": "2.28",
        "XRAY_REALITY_PRIVATE_KEY": "private-value",
        "XRAY_REALITY_PUBLIC_KEY": "public-value",
        "XRAY_REALITY_SHORT_ID": "1234abcd",
    }
    values.update(overrides)
    return {
        "PATH": os.environ.get("PATH", ""),
        "LC_ALL": "C.UTF-8",
        **values,
    }


def _run_renderer(*args: str, env: dict[str, str]):
    return subprocess.run(
        [sys.executable, str(RENDERER), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def _extract_function(script: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}$",
        script,
    )
    assert match, f"missing shell function: {name}"
    return match.group(0)


def _deploy_validation_program() -> str:
    match = re.search(
        r"(?ms)^python3 - <<'PY'\n(.*?)\nPY\n\ncase ",
        _read(DEPLOY),
    )
    assert match, "missing pre-mutation deploy validation program"
    return match.group(1)


def _valid_deploy_inputs(**overrides: str) -> dict[str, str]:
    values = {
        "HY_ENABLE_HTTPS": "1",
        "HY_HTTPS_PORT": "9444",
        "HY_HYSTERIA_VERSION": "v2.9.3",
        "HYSTERIA_PINNED_VERSION": "v2.9.3",
        "HY_XRAY_VERSION": "v26.6.27",
        "XRAY_PINNED_VERSION": "v26.6.27",
        "HY_SERVER_HOST": "panel.example.test",
        "HY_DISPLAY_MULTIPLIER": "2.28",
        "HY_API_SECRET": "a" * 48,
        "HY_OBFS_PASSWORD": "b" * 32,
        "XRAY_REALITY_PRIVATE_KEY": "C" * 43,
        "XRAY_REALITY_PUBLIC_KEY": "D" * 43,
        "XRAY_REALITY_SHORT_ID": "0123456789abcdef",
    }
    values.update(overrides)
    return {"PATH": os.environ.get("PATH", ""), **values}


def test_renderer_preserves_sed_metacharacters_and_replaces_atomically(
    tmp_path,
):
    source = tmp_path / "source.tpl"
    destination = tmp_path / "rendered.conf"
    exotic = r"prefix|middle&suffix\\tail"
    source.write_text(
        "secret=__HY_OBFS_PASSWORD__\nhost=__HY_SERVER_HOST__\n",
        encoding="utf-8",
    )
    destination.write_text("previous-content\n", encoding="utf-8")

    result = _run_renderer(
        str(source),
        str(destination),
        env=_render_env(HY_OBFS_PASSWORD=exotic),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert destination.read_text(encoding="utf-8") == (
        f"secret={exotic}\nhost=panel.example.test\n"
    )
    assert destination.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".rendered.conf.deploy.*"))


def test_renderer_rejects_unknown_source_tokens_and_keeps_values_literal(
    tmp_path,
):
    destination = tmp_path / "rendered.conf"
    destination.write_text("known-good\n", encoding="utf-8")

    unknown = tmp_path / "unknown.tpl"
    unknown.write_text("value=__UNRECOGNIZED_VALUE__\n", encoding="utf-8")
    result = _run_renderer(
        str(unknown),
        str(destination),
        env=_render_env(HY_API_SECRET="do-not-print-this"),
    )
    assert result.returncode == 1
    assert "unknown placeholder" in result.stderr
    assert "do-not-print-this" not in result.stderr
    assert destination.read_text(encoding="utf-8") == "known-good\n"

    literal = tmp_path / "literal.tpl"
    literal.write_text("value=__HY_API_SECRET__\n", encoding="utf-8")
    result = _run_renderer(
        str(literal),
        str(destination),
        env=_render_env(HY_API_SECRET="prefix__LEFTOVER__suffix"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert destination.read_text(encoding="utf-8") == (
        "value=prefix__LEFTOVER__suffix\n"
    )


def test_renderer_ignores_python_dunders_and_all_deploy_inputs_render(
    tmp_path,
):
    deploy = _read(DEPLOY)
    sources = sorted(set(re.findall(
        r'render "\$REPO_DIR/([^"]+)"', deploy,
    )))
    assert sources
    assert any(source.endswith(".py") for source in sources)

    for index, relative in enumerate(sources):
        source = ROOT / relative
        assert source.is_file(), relative
        destination = tmp_path / f"rendered-{index}"
        result = _run_renderer(
            str(source),
            str(destination),
            env=_render_env(
                HY_API_SECRET="literal__name__value",
            ),
        )
        assert result.returncode == 0, (
            relative,
            result.stderr,
        )
        assert destination.is_file()


def test_renderer_rejects_newline_and_raw_env_nul_without_echoing_values(
    tmp_path,
):
    newline_value = "first-line\nsecond-line"
    newline_result = _run_renderer(
        "--validate-environment",
        env=_render_env(HY_OBFS_PASSWORD=newline_value),
    )
    assert newline_result.returncode == 1
    assert "HY_OBFS_PASSWORD" in newline_result.stderr
    assert "first-line" not in newline_result.stderr
    assert "second-line" not in newline_result.stderr

    env_file = tmp_path / ".env"
    env_file.write_bytes(b"HY_API_SECRET=prefix\x00suffix\n")
    env_file.chmod(0o600)
    nul_result = _run_renderer(
        "--validate-env-file",
        str(env_file),
        env=_render_env(),
    )
    assert nul_result.returncode == 1
    assert "NUL byte" in nul_result.stderr
    assert "prefix" not in nul_result.stderr
    assert "suffix" not in nul_result.stderr


def test_strict_dotenv_exec_preserves_literals_without_shell_execution(
    tmp_path,
):
    env_file = tmp_path / ".env"
    shell_marker = tmp_path / "must-not-exist"
    probe = tmp_path / "probe.json"
    literal = (
        f"$(touch {shell_marker});`touch {shell_marker}`"
        r"|amp&back\\slash__TOKEN__"
    )
    env_file.write_text(
        f"HY_API_SECRET={literal}\n"
        "HY_OBFS_PASSWORD=  padded literal  \n"
        "HY_CERTBOT_EMAIL='ops+literal@example.test'\n"
        "XRAY_CLIENT_UUID=legacy-value-is-ignored\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    code = (
        "import json, os, pathlib, sys;"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'secret':os.environ['HY_API_SECRET'],"
        "'obfs':os.environ['HY_OBFS_PASSWORD'],"
        "'email':os.environ['HY_CERTBOT_EMAIL'],"
        "'legacy':os.environ.get('XRAY_CLIENT_UUID'),"
        "'argv':sys.argv"
        "}))"
    )

    result = _run_renderer(
        "--exec-env-file",
        str(env_file),
        sys.executable,
        "-c",
        code,
        str(probe),
        env=_render_env(),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert payload["secret"] == literal
    assert payload["obfs"] == "  padded literal  "
    assert payload["email"] == "ops+literal@example.test"
    assert payload["legacy"] is None
    assert literal not in payload["argv"]
    assert not shell_marker.exists()


def test_strict_dotenv_exec_clears_inherited_deployment_keys(tmp_path):
    env_file = tmp_path / ".env"
    probe = tmp_path / "probe.json"
    env_file.write_text(
        "HY_OBFS_PASSWORD=file-value\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    code = (
        "import json, os, pathlib, sys;"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'missing':os.environ.get('HY_API_SECRET'),"
        "'present':os.environ.get('HY_OBFS_PASSWORD'),"
        "'legacy':os.environ.get('XRAY_CLIENT_UUID')"
        "}))"
    )

    result = _run_renderer(
        "--exec-env-file",
        str(env_file),
        sys.executable,
        "-c",
        code,
        str(probe),
        env=_render_env(
            HY_API_SECRET="must-not-fill-missing",
            HY_OBFS_PASSWORD="parent-value",
            XRAY_CLIENT_UUID="must-not-survive",
        ),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(probe.read_text(encoding="utf-8")) == {
        "missing": None,
        "present": "file-value",
        "legacy": None,
    }


def test_strict_dotenv_exec_uses_minimal_environment_and_blocks_shell_hooks(
    tmp_path,
):
    env_file = tmp_path / ".env"
    bash_env = tmp_path / "bash-env"
    marker = tmp_path / "must-not-run"
    probe = tmp_path / "probe.json"
    canary = "secret-canary-must-not-be-traced"
    env_file.write_text(
        f"HY_API_SECRET={canary}\n"
        "HY_OBFS_PASSWORD=file-obfs\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    bash_env.write_text(
        f"printf hook-ran > {marker}\n",
        encoding="utf-8",
    )
    code = (
        "import json, os, pathlib, sys;"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'secret':os.environ['HY_API_SECRET'],"
        "'bash_env':os.environ.get('BASH_ENV'),"
        "'shellopts':os.environ.get('SHELLOPTS'),"
        "'pythonpath':os.environ.get('PYTHONPATH'),"
        "'ld_preload':os.environ.get('LD_PRELOAD'),"
        "'test_mode':os.environ.get('HY2_HTTPS_TEST_MODE'),"
        "'path':os.environ.get('PATH')"
        "}))"
    )
    environment = _render_env(
        BASH_ENV=str(bash_env),
        SHELLOPTS="braceexpand:hashall:interactive-comments:xtrace",
        PS4=f"+ {canary} ",
        PYTHONPATH=str(tmp_path),
        HY2_HTTPS_TEST_MODE="1",
    )

    result = _run_renderer(
        "--exec-env-file",
        str(env_file),
        "/usr/bin/bash",
        "--noprofile",
        "--norc",
        "-c",
        f"{sys.executable} -c {json.dumps(code)} {probe}",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert canary not in result.stdout
    assert canary not in result.stderr
    assert not marker.exists()
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert payload == {
        "secret": canary,
        "bash_env": None,
        "shellopts": None,
        "pythonpath": None,
        "ld_preload": None,
        "test_mode": None,
        "path": (
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
    }


def test_deploy_entrypoint_uses_privileged_bash_before_any_shell_hook(
    tmp_path,
):
    deploy_copy = tmp_path / "deploy.sh"
    deploy_copy.write_bytes(DEPLOY.read_bytes())
    deploy_copy.chmod(0o755)
    hook = tmp_path / "bash-env"
    marker = tmp_path / "must-not-run"
    hook.write_text(
        f"printf hook > {marker}\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_dirname = fake_bin / "dirname"
    fake_dirname.write_text(
        "#!/usr/bin/bash\n"
        f"printf dirname > {marker}\n"
        "exec /usr/bin/dirname \"$@\"\n",
        encoding="utf-8",
    )
    fake_dirname.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "BASH_ENV": str(hook),
        "SHELLOPTS": (
            "braceexpand:hashall:interactive-comments:xtrace"
        ),
        "BASH_FUNC_exec%%": f"() {{ printf function > {marker}; }}",
    }

    result = subprocess.run(
        [str(deploy_copy)],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "not found" in result.stderr


def test_strict_env_and_nested_deploy_locks_reexec_exactly_once(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    env_file.chmod(0o600)
    lock_dir = tmp_path / "locks"
    harness = tmp_path / "lock-reexec.sh"
    harness.write_text(
        f"""#!/usr/bin/bash -p
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
REPO_DIR={str(ROOT)!r}
ENV_FILE={str(env_file)!r}
LOCK_EXEC="$REPO_DIR/scripts/hy2-lock-exec.py"
DEPLOY_LOCK={str(lock_dir / "deploy.lock")!r}
HTTPS_ACTIVATION_LOCK={str(lock_dir / "https.lock")!r}
if [[ "${{HY2_DEPLOY_ENV_LOADED:-0}}" != 1 ]]; then
  exec /usr/bin/python3 -I \
    "$REPO_DIR/scripts/hy2-render-template.py" \
    --exec-env-file "$ENV_FILE" /usr/bin/bash -p "$0"
fi
/usr/bin/python3 -I "$REPO_DIR/scripts/hy2-render-template.py" \
  --verify-exec-env-file "$ENV_FILE"
if [[ -n "${{HY2_DEPLOY_LOCK_MARKER:-}}" ]]; then
  /usr/bin/python3 -I "$LOCK_EXEC" --lock-file "$DEPLOY_LOCK" \
    --marker-env HY2_DEPLOY_LOCK_MARKER --verify
else
  exec /usr/bin/python3 -I "$LOCK_EXEC" --lock-file "$DEPLOY_LOCK" \
    --timeout 0 --marker-env HY2_DEPLOY_LOCK_MARKER \
    -- /usr/bin/bash -p "$0"
fi
if [[ -n "${{HY2_HTTPS_LOCK_MARKER:-}}" ]]; then
  /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$HTTPS_ACTIVATION_LOCK" \
    --marker-env HY2_HTTPS_LOCK_MARKER --verify
else
  exec /usr/bin/python3 -I "$LOCK_EXEC" \
    --lock-file "$HTTPS_ACTIVATION_LOCK" --timeout 0 \
    --marker-env HY2_HTTPS_LOCK_MARKER \
    -- /usr/bin/bash -p "$0"
fi
unset HY2_DEPLOY_ENV_LOADED
printf 'locked-once\\n'
""",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        [str(harness)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "locked-once\n"
    assert (lock_dir / "deploy.lock").stat().st_mode & 0o777 == 0o600
    assert (lock_dir / "https.lock").stat().st_mode & 0o777 == 0o600


def test_strict_dotenv_reexec_marker_cannot_bypass_file_authority(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HY_API_SECRET=file-secret\n"
        "HY_OBFS_PASSWORD=file-obfs\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/root",
        "USER": "root",
        "LOGNAME": "root",
        "SHELL": "/bin/bash",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HY2_DEPLOY_ENV_LOADED": "1",
        "HY_API_SECRET": "inherited-secret",
        "HY_OBFS_PASSWORD": "file-obfs",
    }

    result = _run_renderer(
        "--verify-exec-env-file",
        str(env_file),
        env=environment,
    )

    assert result.returncode == 1
    assert "does not match the env file: HY_API_SECRET" in result.stderr
    assert "file-secret" not in result.stderr
    assert "inherited-secret" not in result.stderr

    environment["HY_API_SECRET"] = "file-secret"
    result = _run_renderer(
        "--verify-exec-env-file",
        str(env_file),
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_strict_dotenv_rejects_duplicates_unknown_keys_and_broad_mode(
    tmp_path,
):
    cases = (
        (
            "duplicate",
            "HY_API_SECRET=first\nHY_API_SECRET=second\n",
            0o600,
            "duplicate environment key",
        ),
        (
            "unknown",
            "UNSUPPORTED_SECRET=do-not-print\n",
            0o600,
            "unsupported environment key",
        ),
        (
            "mode",
            "HY_API_SECRET=do-not-print\n",
            0o644,
            "permissions are too broad",
        ),
    )
    for name, content, mode, expected in cases:
        env_file = tmp_path / f"{name}.env"
        env_file.write_text(content, encoding="utf-8")
        env_file.chmod(mode)
        result = _run_renderer(
            "--validate-env-file",
            str(env_file),
            env=_render_env(),
        )
        assert result.returncode == 1
        assert expected in result.stderr
        assert "do-not-print" not in result.stderr
        assert "first" not in result.stderr
        assert "second" not in result.stderr

    target = tmp_path / "real.env"
    target.write_text("HY_API_SECRET=do-not-print\n", encoding="utf-8")
    target.chmod(0o600)
    symlink = tmp_path / "linked.env"
    symlink.symlink_to(target)
    result = _run_renderer(
        "--validate-env-file",
        str(symlink),
        env=_render_env(),
    )
    assert result.returncode == 1
    assert "could not read the environment file" in result.stderr
    assert "do-not-print" not in result.stderr


def test_deploy_uses_renderer_without_passing_credentials_in_argv():
    deploy = _read(DEPLOY)
    render = _extract_function(deploy, "render")

    assert "sed" not in render
    assert 'python3 "$REPO_DIR/scripts/hy2-render-template.py" "$src" "$dst"' in render
    for secret_name in (
        "HY_API_SECRET",
        "HY_OBFS_PASSWORD",
        "XRAY_REALITY_PRIVATE_KEY",
    ):
        assert f"${{{secret_name}}}" not in render
        assert f"${secret_name}" not in render

    strict_exec = deploy.index(
        'exec /usr/bin/python3 -I '
        '"$REPO_DIR/scripts/hy2-render-template.py"'
    )
    exec_mode = deploy.index(
        '--exec-env-file "$ENV_FILE" /usr/bin/bash -p "$0"',
        strict_exec,
    )
    verify_mode = deploy.index(
        '--verify-exec-env-file "$ENV_FILE"',
        exec_mode,
    )
    validate_values = deploy.index('validate_template_value "$v"')
    mutable_start = deploy.index("capture_service_state")
    assert strict_exec < exec_mode < verify_mode < validate_values
    assert 'set -a; . "$ENV_FILE"; set +a' not in deploy
    assert 'od -An -v -t x1 -- "$ENV_FILE"' not in deploy
    assert validate_values < deploy.index("capture_service_state", mutable_start + 1)


def test_all_deploy_inputs_fail_before_service_or_package_mutation():
    deploy = _read(DEPLOY)
    capture = deploy.index("capture_service_state\nbegin_rollback_snapshot")
    apt = deploy.index("apt-get update -y")
    validation = deploy.index("python3 - <<'PY'")
    architecture = deploy.index('case "$(uname -m)"', validation)

    assert validation < architecture < capture < apt
    for marker in (
        "HY_ENABLE_HTTPS must be 0 or 1",
        "HY_HTTPS_PORT must be an integer",
        "HY_HYSTERIA_VERSION is not in the checksum allowlist",
        "HY_XRAY_VERSION is not in the checksum allowlist",
        "HY_DISPLAY_MULTIPLIER must be between",
        "HY_SERVER_HOST must be a canonical IPv4 address or DNS name",
        "XRAY_REALITY_SHORT_ID must be",
    ):
        assert deploy.index(marker) < capture


def test_deploy_input_validator_accepts_normal_generated_values():
    program = _deploy_validation_program()
    for host in ("panel.example.test", "203.0.113.9"):
        result = subprocess.run(
            [sys.executable, "-c", program],
            env=_valid_deploy_inputs(HY_SERVER_HOST=host),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (host, result.stderr)


def test_deploy_input_validator_rejects_injection_and_protocol_invalid_values():
    program = _deploy_validation_program()
    cases = (
        {"HY_SERVER_HOST": "panel.example.test;#"},
        {"HY_SERVER_HOST": "Panel.Example.Test"},
        {"HY_SERVER_HOST": "-bad.example.test"},
        {"HY_SERVER_HOST": "192.168.001.010"},
        {"HY_SERVER_HOST": "localhost"},
        {"HY_API_SECRET": "a" * 24 + "#comment"},
        {"HY_OBFS_PASSWORD": "safe-but-'quoted-token"},
        {"XRAY_REALITY_PRIVATE_KEY": "C" * 42 + "#"},
        {"XRAY_REALITY_PUBLIC_KEY": "D" * 42 + '"'},
        {"XRAY_REALITY_SHORT_ID": "abc"},
        {"XRAY_REALITY_SHORT_ID": "aa;bb"},
        {"HY_ENABLE_HTTPS": "true"},
        {"HY_HTTPS_PORT": "443"},
        {"HY_HTTPS_PORT": "8081"},
        {"HY_HTTPS_PORT": "8082"},
        {"HY_HTTPS_PORT": "8443"},
        {"HY_HTTPS_PORT": "9443"},
        {"HY_HTTPS_PORT": "10085"},
        {"HY_HTTPS_PORT": "25413"},
        {"HY_HTTPS_PORT": "08080"},
        {"HY_HYSTERIA_VERSION": "v2.9.4"},
        {"HY_XRAY_VERSION": "latest;#"},
        {"HY_DISPLAY_MULTIPLIER": "nan"},
    )
    for overrides in cases:
        result = subprocess.run(
            [sys.executable, "-c", program],
            env=_valid_deploy_inputs(**overrides),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, overrides
        for value in overrides.values():
            assert value not in result.stderr


def test_tuic_release_maps_architectures_and_verifies_pinned_hash_before_install():
    deploy = _read(DEPLOY)

    assert "x86_64|amd64)" in deploy
    assert "tuic_target=x86_64-unknown-linux-gnu" in deploy
    assert "aarch64|arm64)" in deploy
    assert "tuic_target=aarch64-unknown-linux-gnu" in deploy
    assert 'die "Unsupported architecture: $(uname -m)"' in deploy
    assert (
        "TUIC_AMD64_SHA256="
        "7cd85d8857cef7990ce067d8b48595e6532f0440522529d796d3a8b2f29e7b9f"
        in deploy
    )
    assert (
        "TUIC_ARM64_SHA256="
        "0403ba2a5f3e463f000b5db897baad9f5d077ef304e0f8d537334b6e4c324f4a"
        in deploy
    )

    download = deploy.index(
        "https://github.com/tuic-protocol/tuic/releases/download/"
        "tuic-server-${TUIC_VERSION}/${tuic_asset}"
    )
    verify = deploy.index(
        'printf \'%s  %s\\n\' "$tuic_sha256" "$TUIC_DOWNLOAD" | sha256sum -c -',
        download,
    )
    stage = deploy.index(
        'install -m 755 "$TUIC_DOWNLOAD" "$TUIC_CANDIDATE"',
        verify,
    )
    commit = deploy.index(
        '"$TUIC_CANDIDATE" /usr/local/bin/tuic-server',
        stage,
    )
    assert download < verify < stage < commit
    assert "tuic_url.sha256sum" not in deploy


def test_hysteria_release_uses_repository_pinned_architecture_hashes():
    deploy = _read(DEPLOY)

    assert "readonly HYSTERIA_PINNED_VERSION=v2.9.3" in deploy
    assert (
        "readonly HYSTERIA_AMD64_SHA256="
        "66dbdb0608f25f3057b433afe975a9fc1af2ca8e512479e294988b3ef363d6c1"
        in deploy
    )
    assert (
        "readonly HYSTERIA_ARM64_SHA256="
        "938df06c5a8ed001dbc38718b5385b5fcbd721669f1163518ea8e738866865f2"
        in deploy
    )
    assert (
        '!= os.environ["HYSTERIA_PINNED_VERSION"]'
        in deploy
    )
    download = deploy.index(
        '"$hysteria_base/$hysteria_asset" -o "$tmpdir/$hysteria_asset"'
    )
    verify = deploy.index(
        'printf \'%s  %s\\n\' "$hysteria_sha256"',
        download,
    )
    install = deploy.index(
        'install -m 755 "$tmpdir/$hysteria_asset" '
        '"$HYSTERIA_CANDIDATE"',
        verify,
    )
    assert download < verify < install
    assert "$hysteria_base/hashes.txt" not in deploy
    installed_digest = deploy.index(
        "installed_hysteria_sha256="
    )
    installed_metadata = deploy.index(
        "installed_hysteria_metadata=",
        installed_digest,
    )
    metadata_gate = deploy.index(
        '"$installed_hysteria_metadata" == "0:0:755:1"',
        installed_metadata,
    )
    reinstall_gate = deploy.index(
        '"$installed_hysteria_sha256" != "$hysteria_sha256"',
        installed_digest,
    )
    staged_install = deploy.index(
        'HYSTERIA_CANDIDATE="$(mktemp '
        '/usr/local/bin/.hysteria.deploy.XXXXXX)"',
        reinstall_gate,
    )
    atomic_commit = deploy.index(
        '"$HYSTERIA_CANDIDATE" /usr/local/bin/hysteria',
        staged_install,
    )
    assert (
        installed_digest
        < installed_metadata
        < metadata_gate
        < reinstall_gate
        < staged_install
        < atomic_commit
    )
    inspect_block = deploy[installed_digest:reinstall_gate]
    assert "hysteria version" not in inspect_block
    assert "/usr/local/bin/hysteria version" not in deploy


def _write_readiness_harness(tmp_path: Path, sequence: list[str]) -> Path:
    function = _extract_function(_read(DEPLOY), "wait_for_stable_readiness")
    harness = tmp_path / "readiness-harness.sh"
    quoted = " ".join(sequence)
    harness.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
required_active_units=(test.service)
SEQUENCE=("""
        + quoted
        + """)
CALLS=0
systemctl() {
  local state="${SEQUENCE[$CALLS]:-down}"
  CALLS=$((CALLS + 1))
  [[ "$state" == "up" ]]
}
curl() { return 0; }
sleep() { :; }
"""
        + function
        + """
if wait_for_stable_readiness 3 "${#SEQUENCE[@]}" 0; then
  printf 'ready %s\n' "$CALLS"
else
  printf 'not-ready %s\n' "$CALLS"
  exit 1
fi
""",
        encoding="utf-8",
    )
    return harness


def test_readiness_requires_three_consecutive_full_stack_observations(tmp_path):
    harness = _write_readiness_harness(
        tmp_path,
        ["up", "up", "down", "up", "up", "up"],
    )
    result = subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ready 6\n"

    flapping = _write_readiness_harness(
        tmp_path,
        ["up", "down", "up", "down", "up", "down"],
    )
    result = subprocess.run(
        ["bash", str(flapping)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == "not-ready 6\n"


def test_final_success_follows_stable_readiness_gate():
    deploy = _read(DEPLOY)
    gate = deploy.index("wait_for_stable_readiness 3 15 1")
    success = deploy.index("DEPLOY_SUCCEEDED=1", gate)
    assert gate < success
    assert "three consecutive healthy observations" in deploy[gate:success]
