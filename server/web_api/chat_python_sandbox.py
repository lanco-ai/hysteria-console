"""Python tool bridge. User code is sent exclusively to an isolated Docker container."""
import asyncio
import json
import os
from uuid import uuid4

from .chat_workspace_store import WorkspaceError, encoded

_SLOT = asyncio.Lock()
DEFAULT_IMAGE = 'hy2-learning-python:1'


async def run_python(code, files=None):
    if _SLOT.locked():
        raise WorkspaceError('python_busy', 503)
    async with _SLOT:
        name = 'hy2-learning-' + uuid4().hex
        image = os.environ.get('HY2_PYTHON_IMAGE', DEFAULT_IMAGE)
        args = ['docker', 'run', '--name', name, '--rm', '-i', '--network', 'none',
                '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--pids-limit', '32',
                '--memory', '192m', '--memory-swap', '192m', '--cpus', '0.5',
                '--ulimit', 'nofile=64:64', '--ulimit', 'fsize=8388608:8388608',
                '--tmpfs', '/workspace:rw,noexec,nosuid,size=64m,uid=65534,gid=65534,mode=700',
                '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m,mode=1777',
                '--workdir', '/workspace', '--env', 'HOME=/tmp', '--env', 'MPLCONFIGDIR=/tmp/matplotlib',
                '--env', 'MPLBACKEND=Agg', '--env', 'OPENBLAS_NUM_THREADS=1', '--env', 'OMP_NUM_THREADS=1',
                image, 'python', '-I', '/runner.py']
        process = None
        try:
            async with asyncio.timeout(40):
                process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                                                               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                payload = encoded({'code': code, 'files': files or []}).encode()
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                raw = bytearray()
                while chunk := await process.stdout.read(16384):
                    raw.extend(chunk)
                    if len(raw) > 10 * 1024 * 1024:
                        raise WorkspaceError('python_output_limit')
                await process.wait()
                if process.returncode:
                    raise WorkspaceError('python_unavailable', 503)
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise WorkspaceError('python_invalid_output')
                return result
        except TimeoutError:
            raise WorkspaceError('python_timeout') from None
        except (OSError, ValueError, BrokenPipeError):
            raise WorkspaceError('python_unavailable', 503) from None
        finally:
            # Only the unique container created by this request is removed.
            try:
                cleanup = await asyncio.create_subprocess_exec('docker', 'rm', '-f', name,
                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(cleanup.wait(), 10)
            except (OSError, TimeoutError):
                pass
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
