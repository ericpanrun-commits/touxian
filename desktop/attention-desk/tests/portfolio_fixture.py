"""Isolated browser harness; never starts the real worker, database or cloud client."""
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

from server.engineering_portfolio import Portfolio, REPOSITORY, router

directory = Path(os.environ['ERIC_PORTFOLIO_TEST_DIR']).resolve()
if directory.is_relative_to(REPOSITORY) or not (directory / '.synthetic-portfolio-test').is_file():
    raise RuntimeError('An explicitly marked synthetic fixture outside Git is required.')
root = directory / 'Engineering-Portfolio'
root.mkdir(exist_ok=True)
app = FastAPI()
app.state.engineering_portfolio = Portfolio(directory / 'settings.json')
if not app.state.engineering_portfolio.settings.exists():
    app.state.engineering_portfolio.configure(str(root))
app.include_router(router)
opened = []
native_startfile = getattr(os, 'startfile', None)


def open_folder(path, operation):
    opened.append({'path': path, 'operation': operation})
    if os.environ.get('ERIC_PORTFOLIO_TEST_EXPLORER') == '1':
        native_startfile(path, operation)


os.startfile = open_folder


@app.get('/__test__/state')
def state():
    return {'synthetic_only': True, 'opened': opened}


@app.get('/api/{path:path}')
def unrelated(path: str):
    # Keep unrelated legacy startup reads inert; no main.py side effects.
    return {'status': 'ok', 'items': [], 'sources': [], 'total': 0}


dist = Path(__file__).resolve().parents[1] / 'dist'


@app.get('/{path:path}')
def frontend(path: str):
    file = (dist / path).resolve()
    if not file.is_relative_to(dist) or not file.is_file():
        file = dist / 'index.html'
    return FileResponse(file)


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('ERIC_PORTFOLIO_TEST_PORT', '18786')), log_level='warning')
