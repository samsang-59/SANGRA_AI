"""Starts the real server process with the FakeWorkflow in place of the agent (tests only, B5-R06).

    python -m tests.fake_server     (same settings as `python -m app`, from the environment)
"""

import uvicorn

import app.main as main
from app.core.config import load_settings
from app.core.logging import setup_logging
from tests.fake_agent import FakeWorkflow

if __name__ == "__main__":
    main.build_workflow = lambda app, progress, checkpointer: FakeWorkflow(progress, pause_forever=True)  # type: ignore
    settings = load_settings()
    setup_logging(settings.log_level, settings.secret_values())
    uvicorn.run(main.create_app(settings), host=settings.host, port=settings.port, log_config=None)
