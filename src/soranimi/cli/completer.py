from prompt_toolkit.completion import Completer, Completion

COMMANDS = {
    "/help": "显示帮助",
    "/exit": "结束会话",
    "/compact": "压缩上下文（可附保留重点）",
    "/undo": "撤销最近一次文件修改",
    "/diff": "显示会话文件改动",
    "/sessions": "列出会话",
    "/checkpoints": "列出图状态检查点",
    "/rewind": "回到图检查点（不回滚文件）",
    "/plan": "显示当前任务计划",
    "/memory": "显示项目偏好",
}


class AgentCompleter(Completer):
    def __init__(self, workspace):
        self.workspace = workspace

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for command, help_text in COMMANDS.items():
                if command.startswith(text):
                    yield Completion(command, start_position=-len(text), display_meta=help_text)
        word = document.get_word_before_cursor(WORD=True)
        if word.startswith("@"):
            query = word[1:]
            count = 0
            for path in self.workspace.files():
                relative = self.workspace.relative(path)
                if relative.startswith(query):
                    yield Completion("@" + relative, start_position=-len(word))
                    count += 1
                    if count >= 100:
                        break
