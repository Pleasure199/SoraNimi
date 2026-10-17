from langchain_core.tools import tool


def make_bash(ctx):
    @tool
    def bash(command: str, timeout: int = 120) -> dict:
        """Run a command in the workspace with bounded output and timeout (1-600 s).

        On Windows the local shell is PowerShell; on Unix it is /bin/sh. Each
        invocation starts in the workspace; shell variables/cd do not persist.
        Permission approval is enforced by the graph before execution. Local
        subprocess execution is trusted code execution, without OS isolation.
        """
        if not 1 <= timeout <= 600:
            raise ValueError("timeout must be between 1 and 600 seconds")
        return ctx.executor.execute(command, timeout).as_dict()

    return bash
