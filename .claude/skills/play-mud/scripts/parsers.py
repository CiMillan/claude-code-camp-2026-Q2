"""Best-effort parsers for CircleMUD command output.

Each parser returns None if the text doesn't look like its target block, so
callers can try several against arbitrary command output without special
handling. Never raises on malformed input — worst case is a None.
"""
import re

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


SCORE_PATTERNS = {
    "age": re.compile(r"You are (\d+) years old", re.I),
    "hp": re.compile(r"(\d+)\((\d+)\) hit", re.I),
    "mana": re.compile(r"(\d+)\((\d+)\) mana", re.I),
    "movement": re.compile(r"(\d+)\((\d+)\) movement", re.I),
    "ac": re.compile(r"armor class is (-?\d+)/(-?\d+)", re.I),
    "alignment": re.compile(r"alignment is (-?\d+)", re.I),
    "exp": re.compile(r"have (?:scored )?(\d+) exp\b", re.I),
    "exp_needed": re.compile(r"You need (\d+) exp to reach", re.I),
    "gold": re.compile(r"(\d+) gold coins?", re.I),
    "quest_points": re.compile(r"(\d+) quest[- ]?points?", re.I),
    "level_title": re.compile(r"ranks you as (.+?)\s*\(level (\d+)\)", re.I),
}


def parse_score(text: str):
    """Parse a `score` command response. Returns a dict of whatever fields
    were found, or None if this doesn't look like a score block at all."""
    clean = strip_ansi(text)
    if "years old" not in clean and "armor class" not in clean.lower():
        return None

    result = {}
    m = SCORE_PATTERNS["age"].search(clean)
    if m:
        result["age"] = int(m.group(1))
    m = SCORE_PATTERNS["hp"].search(clean)
    if m:
        result["hp"] = {"current": int(m.group(1)), "max": int(m.group(2))}
    m = SCORE_PATTERNS["mana"].search(clean)
    if m:
        result["mana"] = {"current": int(m.group(1)), "max": int(m.group(2))}
    m = SCORE_PATTERNS["movement"].search(clean)
    if m:
        result["movement"] = {"current": int(m.group(1)), "max": int(m.group(2))}
    m = SCORE_PATTERNS["ac"].search(clean)
    if m:
        result["ac"] = f"{m.group(1)}/{m.group(2)}"
    m = SCORE_PATTERNS["alignment"].search(clean)
    if m:
        result["alignment"] = int(m.group(1))
    m = SCORE_PATTERNS["exp"].search(clean)
    if m:
        result["exp"] = int(m.group(1))
    m = SCORE_PATTERNS["exp_needed"].search(clean)
    if m:
        result["exp_needed"] = int(m.group(1))
    m = SCORE_PATTERNS["gold"].search(clean)
    if m:
        result["gold"] = int(m.group(1))
    m = SCORE_PATTERNS["quest_points"].search(clean)
    if m:
        result["quest_points"] = int(m.group(1))
    m = SCORE_PATTERNS["level_title"].search(clean)
    if m:
        result["title"] = m.group(1).strip()
        result["level"] = int(m.group(2))

    return result or None


INVENTORY_HEADER = re.compile(r"you are carrying:", re.I)
EQUIPMENT_HEADER = re.compile(r"you are using:", re.I)


def _parse_item_list(text: str, header_pattern) -> list:
    clean = strip_ansi(text)
    m = header_pattern.search(clean)
    if not m:
        return None
    lines = clean[m.end():].splitlines()
    items = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Stop at the next prompt line (e.g. "23H 100M 83V ... >")
        if re.match(r"^\d+H\s+\d+M\s+\d+V\b", line):
            break
        items.append(line)
    return items


def parse_inventory(text: str):
    return _parse_item_list(text, INVENTORY_HEADER)


def parse_equipment(text: str):
    return _parse_item_list(text, EQUIPMENT_HEADER)


EXITS_BLOCK_RE = re.compile(
    r"Obvious exits:\s*\n((?:(?:north|east|south|west|up|down)\s*-\s*.+\n?)+)",
    re.I,
)
EXIT_LINE_RE = re.compile(r"(north|east|south|west|up|down)\s*-\s*(.+)", re.I)
# CircleMUD's prompt doesn't force a newline before the next room's text, so
# a line can look like "23H 100M 83V (news) (motd) > The Bakery" with real
# content sharing the line with the prompt. Strip prompt occurrences
# in-place (wherever they land) rather than discarding whole lines.
PROMPT_INLINE_RE = re.compile(r"\d+H\s+\d+M\s+\d+V(?:\s*\([^)]*\))*\s*>\s*")
# The quiet-window read can also leave stray single-character fragments
# (e.g. a lone ".") at a chunk boundary — not real content, just noise.
NOISE_LINE_RE = re.compile(r"^[.\-_]{1,3}$")


def parse_exits(text: str):
    """Parse an explicit `exits` command response ("Obvious exits:\\n
    north - ...") anywhere in the given text. Independent of parse_room so
    it can be merged into whatever room is already tracked as current, even
    when it arrives in its own chunk with no room name alongside it."""
    clean = strip_ansi(text)
    m = EXITS_BLOCK_RE.search(clean)
    if not m:
        return None
    exits = {}
    for line in m.group(1).splitlines():
        em = EXIT_LINE_RE.match(line.strip())
        if em:
            exits[em.group(1).lower()] = em.group(2).strip()
    return exits or None


ROOM_NAME_ANSI_RE = re.compile(r"\x1b\[0;33m(.+?)\x1b\[0m")


def parse_room(text: str):
    """Parse a `look`/room-entry block: a name line, a description
    paragraph, and (if the `exits` command was also run in the same chunk)
    an explicit "Obvious exits:" list. Returns None if no room name is
    found — in particular, a chunk that is *only* an "Obvious exits:" block
    (no name) returns None here; use parse_exits for that case instead.

    Ordinary command output (score, inventory, "Alas, you cannot go that
    way...") has no ANSI at all, so scanning for "the first plausible-
    looking line" produces false positives on almost anything. CircleMUD
    consistently color-codes the room name itself as "\\x1b[0;33m<name>
    \\x1b[0m" (the same yellow it also uses for NPCs/objects present, but
    the room name is always the first such span in a look/movement
    response) — that's the one signal that's actually specific to a room
    being displayed, so we require it rather than guessing from position.
    This assumes ANSI color is on, which these scripts never disable.

    This intentionally does NOT try to resolve the room to a ground-truth
    id — see room_index.py / state.py for that. It just extracts what
    CircleMUD printed.
    """
    m = ROOM_NAME_ANSI_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip()

    remainder = strip_ansi(text[m.end():])
    remainder = PROMPT_INLINE_RE.sub("", remainder)
    remainder = remainder.lstrip("\r\n")  # the name's own line ends right at the color reset

    desc_lines = []
    for line in remainder.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("[ Exits") or NOISE_LINE_RE.match(stripped):
            break
        if stripped.lower().startswith("obvious exits"):
            break
        desc_lines.append(stripped)
    desc = " ".join(desc_lines).strip()

    result = {"name": name, "desc": desc}

    exits = parse_exits(text)
    if exits:
        result["exits"] = exits

    return result
