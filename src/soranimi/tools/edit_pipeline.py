"""Unique replacements preserve encoding and newline style."""

import re
from bisect import bisect_left

QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


def replace_unique(content: str, old: str, new: str) -> str:
    if not old:
        raise ValueError("old_string must not be empty; use write_file to create a file")
    old = old.replace("\r\n", "\n").replace("\r", "\n")
    new = new.replace("\r\n", "\n").replace("\r", "\n")
    if old == new:
        raise ValueError("old_string and new_string are identical")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    count = normalized.count(old)
    if count > 1:
        raise ValueError(f"old_string matches {count} times; provide more unique context")
    if count == 1:
        begin = normalized.index(old)
        end = begin + len(old)
    else:
        # Quote normalization preserves positions; never normalize unrelated code.
        canonical = normalized.translate(QUOTES)
        needle = old.translate(QUOTES)
        count = canonical.count(needle)
        if count > 1:
            raise ValueError(f"Normalized old_string matches {count} times")
        if count == 1:
            begin = canonical.index(needle)
            end = begin + len(needle)
        else:
            # Flex only horizontal whitespace: never match across unrelated lines.
            pattern = re.escape(old)
            pattern = re.sub(r"(?:\\ |\\\t)+", r"[ \\t]+", pattern)
            matches = list(re.finditer(pattern, normalized))
            if len(matches) != 1:
                raise ValueError(
                    f"old_string matches {len(matches)} times; read_file again and supply exact unique text"
                )
            match = matches[0]
            begin, end = match.span()
    # Match in normalized text, but splice into the original so unrelated mixed
    # line endings are never rewritten. Every CRLF removed one character.
    crlf = [match.start() - index for index, match in enumerate(re.finditer("\r\n", content))]
    begin += bisect_left(crlf, begin)
    end += bisect_left(crlf, end)
    following = re.search(r"\r\n|\r|\n", content[begin:])
    previous = list(re.finditer(r"\r\n|\r|\n", content[:begin])) if following is None else []
    newline = following.group() if following else previous[-1].group() if previous else "\n"
    return content[:begin] + new.replace("\n", newline) + content[end:]
