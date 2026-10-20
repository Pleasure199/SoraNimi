"""Bridge async MCP tools into the synchronous CLI on one owned event loop."""

import asyncio
import json
import threading
from pathlib import Path

from langchain_core.tools import StructuredTool


class MCPConnection:
    def __init__(self, config_path: Path, excluded_env=()):
        self.path = config_path
        self.excluded_env = {name.upper() for name in excluded_env}
        self.loop = None
        self.thread = None

    def __enter__(self):
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError as exc:
            raise ValueError("MCP requires: pip install 'soranimi[mcp]'") from exc
        config = json.loads(self.path.read_text(encoding="utf-8"))
        connections = config.get("mcpServers", config)
        if not isinstance(connections, dict):
            raise ValueError("MCP config must contain a server mapping")
        for name, connection in connections.items():
            if not isinstance(connection, dict) or connection.get("transport") not in {
                "stdio",
                "http",
                "streamable_http",
                "sse",
            }:
                raise ValueError(f"MCP server {name}: set transport to stdio, http or sse")
            if connection["transport"] == "stdio":
                # Do not implicitly pass provider credentials to a configured server.
                import os

                defaults = {
                    key: value
                    for key, value in os.environ.items()
                    if key.upper() not in self.excluded_env
                    and not any(
                        word in key.upper() for word in ("API_KEY", "SECRET", "TOKEN", "PASSWORD")
                    )
                }
                connection["env"] = {**defaults, **connection.get("env", {})}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.client = MultiServerMCPClient(connections, tool_name_prefix=True)
        try:
            remote = self.call(self.client.get_tools())
            self.tools = [self.wrap(tool) for tool in remote]
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=120)
        except BaseException:
            future.cancel()
            raise

    def wrap(self, remote):
        def invoke(**kwargs):
            return self.call(remote.ainvoke(kwargs))

        return StructuredTool(
            name=f"mcp_{remote.name}",
            description=remote.description or remote.name,
            args_schema=remote.args_schema,
            func=invoke,
            handle_tool_error=True,
        )

    def __exit__(self, *args):
        if self.loop is not None:

            async def cleanup():
                tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self.loop.shutdown_asyncgens()

            self.call(cleanup())
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.loop.close()
            self.loop = None
