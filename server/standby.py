"""Pre-cutover app: no business stores, workers or request handlers are created."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from web_api.app import _SecurityAndErrorBoundary


def standby_enabled(environ, scheduler_key):
    # Existing scheduler-off installations also fail closed on upgrade. An
    # operator may explicitly run active APIs with schedulers off after cutover.
    value = environ.get('LANCOAI_STANDBY')
    if value is None:
        return environ.get(scheduler_key, '0') != '1'
    if value not in ('0', '1'):
        raise ValueError('LANCOAI_STANDBY must be 0 or 1')
    return value == '1'


def create_standby_app(react_dist, prefix):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_SecurityAndErrorBoundary)

    @app.api_route('/healthz', methods=['GET', 'HEAD'])
    def health():
        return {'ok': True, 'standby': True}

    app.mount(prefix, StaticFiles(directory=react_dist), name='standby-static')

    @app.api_route(
        '/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
    )
    def unavailable(path):
        return JSONResponse(
            {'error': 'service_standby'}, status_code=503, headers={'Retry-After': '30'}
        )

    return app
