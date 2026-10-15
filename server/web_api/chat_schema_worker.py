"""Validate untrusted MCP schemas with hard CPU/memory limits and no remote refs."""
import json
import resource
import sys

resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024,) * 2)

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry
    value = json.loads(sys.stdin.buffer.read(256 * 1024))
    def refuse(uri):
        raise ValueError('remote reference disabled')
    Draft202012Validator.check_schema(value['schema'])
    Draft202012Validator(value['schema'], registry=Registry(retrieve=refuse)).validate(value['arguments'])
    sys.stdout.write('ok')
except Exception:
    raise SystemExit(1)
