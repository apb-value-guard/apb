"""Read render.yaml's simple key: value pairs without adding a YAML dependency."""

import re


def values(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    key = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        env_key = re.match(r"\s*- key:\s*(\S+)", line)
        if env_key:
            key = env_key.group(1)
            continue
        env_value = re.match(r"\s*value:\s*(.+)", line)
        if env_value and key:
            found[key] = env_value.group(1).strip().strip('"')
            key = None
            continue
        pair = re.match(r"\s*(?:- )?(\w+):\s*(.+)", line)
        if pair:
            found[pair.group(1)] = pair.group(2).strip().strip('"')
    return found
