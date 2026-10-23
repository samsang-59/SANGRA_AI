"""The LangGraph workflow behind the AgentRunner, and the LangGraph checkpointer (its own connections).

start(run_id) runs the graph from the first step with thread_id = the run id. resume(run_id) continues a paused
graph, but only when LangGraph really holds a paused snapshot for that run: resuming a thread that ended (or whose
snapshot was deleted) would start the graph again from the top with an empty state, so it is refused (B9-C04).
"""

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import build_graph
from app.agent.nodes.steps import AgentServices, AgentSteps
from app.core.config import Settings

logger = logging.getLogger("app.agent")


def create_checkpointer_pool(settings: Settings) -> AsyncConnectionPool:
    """LangGraph's checkpointer uses its OWN connections, with the settings it needs (autocommit, no prepared
    statements, dict rows); our repositories never share them (04_backend_design.md Layer 7, item 2)."""
    return AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(), min_size=1, max_size=5,
        timeout=settings.db_pool_timeout_seconds, open=False, name="langgraph",
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection)


def setup_langgraph_tables(database_url: str) -> None:
    """Creates or updates LangGraph's own tables (its setup() call). Run by the manual migration command."""
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver

    with psycopg.connect(database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row) as conn:
        PostgresSaver(conn).setup()


# durability="sync": every checkpoint is saved before the graph goes on (or returns, or fails). With the default
# ("async") the saves run in the background, so after an error a late save could land after the snapshot was
# deleted and leave rows behind (found by B9-X11).
def _config(run_id: int) -> dict:
    return {"configurable": {"thread_id": str(run_id)}, "recursion_limit": 500}


class LangGraphWorkflow:
    def __init__(self, services: AgentServices, checkpointer: AsyncPostgresSaver):
        self.services = services
        self.checkpointer = checkpointer
        self.graph = build_graph(AgentSteps(services)).compile(checkpointer=checkpointer)

    async def start(self, run_id: int) -> None:
        run = await self.services.progress.load_run(run_id)
        if run is None:
            return
        await self.graph.ainvoke({"run_id": run["id"], "user_id": run["user_id"],
                                  "bug_description": run["bug_description"], "language": run["language"],
                                  "test_command": run["test_command"]}, _config(run_id), durability="sync")

    async def is_paused(self, run_id: int) -> bool:
        snapshot = await self.graph.aget_state(_config(run_id))
        return bool(snapshot.next)

    async def resume(self, run_id: int, value: object) -> None:
        if not await self.is_paused(run_id):
            logger.warning("Run %s: nothing to resume (the graph is not paused)", run_id)
            return
        await self.graph.ainvoke(Command(resume=value), _config(run_id), durability="sync")

    async def delete_snapshots(self, run_id: int) -> None:
        await self.checkpointer.adelete_thread(str(run_id))
