import json


def estimate_tokens(messages) -> int:
    """Conservative estimate for mixed CJK/code, including tool arguments."""
    total = 0
    for message in messages:
        text = json.dumps(message.model_dump(), ensure_ascii=False, default=str)
        cjk = sum(ord(char) > 127 for char in text)
        total += (len(text) - cjk + 2) // 3 + cjk + 8
    return total
