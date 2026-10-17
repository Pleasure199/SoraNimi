"""Unique replacements preserve encoding and newline style."""

import re

QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


def replace_unique(content: str, old: str, new: str) -> str:
    if not old:
        raise ValueError("old_string must not be empty; use write_file to create a file")
    old = old.replace("\r\n", "\n")
    new = new.replace("\r\n", "\n")
    if old == new:
        raise ValueError("old_string and new_string are identical")
    newline = "\r\n" if "\r\n" in content else "\n"
    normalized = content.replace("\r\n", "\n")
    count = normalized.count(old)
    if count > 1:
        raise ValueError(f"old_string matches {count} times; provide more unique context")
    if count == 1:
        updated = normalized.replace(old, new, 1)
    else:
        # Quote normalization preserves positions; never normalize unrelated code.
        canonical = normalized.translate(QUOTES)
        needle = old.translate(QUOTES)
        count = canonical.count(needle)
        if count > 1:
            raise ValueError(f"Normalized old_string matches {count} times")
        if count == 1:
            begin = canonical.index(needle)
            updated = normalized[:begin] + new + normalized[begin + len(needle) :]
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
            updated = normalized[: match.start()] + new + normalized[match.end() :]
    return updated.replace("\n", newline)
