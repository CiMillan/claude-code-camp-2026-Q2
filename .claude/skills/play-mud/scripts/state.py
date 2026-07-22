"""Persistent player/world state for the play-mud skill.

Each script invocation is a fresh process, so continuity across commands
lives entirely in state/*.json. This module is the only thing that reads or
writes those files — always atomically (write to a .tmp file, then
os.replace), so a crash mid-write can never corrupt the previous good state.

Room identity is resolved two ways, in priority order:
  1. Movement: if the previous room is known and the command that produced
     this output was a direction, room_index.json's ground-truth exits give
     the exact next room id — no ambiguity, no text matching needed.
  2. Text match: hash the room's (name, desc) and look it up in
     room_index.json's by_hash table. Unique hit -> resolved. Multiple hits
     -> genuinely ambiguous (some zones, e.g. mazes, reuse identical room
     text on purpose) -> recorded as a candidate list, not guessed at.
     No hit -> the room isn't in the parsed world data (e.g. the mounted
     world was hand-edited) -> recorded as unresolved, keyed by a local
     hash of its own text so repeat visits still dedupe.
"""
import json
import os
import re
import time

import parsers
from room_index import content_key

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")
PLAYER_PATH = os.path.join(STATE_DIR, "player.json")
WORLD_PATH = os.path.join(STATE_DIR, "world.json")
ROOM_INDEX_PATH = os.path.join(STATE_DIR, "room_index.json")
PLAYER_MD_PATH = os.path.join(STATE_DIR, "player.md")
STATUS_MD_PATH = os.path.join(STATE_DIR, "status.md")

DIRECTION_ALIASES = {
    "n": "north", "north": "north",
    "e": "east", "east": "east",
    "s": "south", "south": "south",
    "w": "west", "west": "west",
    "u": "up", "up": "up",
    "d": "down", "down": "down",
}


def _now():
    return time.time()


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def load_player():
    return _read_json(PLAYER_PATH, {
        "current_room_id": None,
        "room_status": None,  # "resolved" | "ambiguous" | "unresolved" | None
    })


def load_world():
    return _read_json(WORLD_PATH, {"visited": {}, "unresolved": {}, "ambiguous": {}})


def load_room_index():
    return _read_json(ROOM_INDEX_PATH, None)


def normalize_direction(command: str):
    return DIRECTION_ALIASES.get(command.strip().lower())


def resolve_room(command, room, player, room_index):
    """Returns a dict: {"status": "resolved"|"ambiguous"|"unresolved",
    "room_id": id or None, "candidates": [...] (only for ambiguous)}."""
    key = content_key(room["name"], room["desc"])
    candidates = room_index["by_hash"].get(key, []) if room_index else []

    prev_id = player.get("current_room_id")
    direction = normalize_direction(command)
    if prev_id is not None and direction and room_index:
        prev_exits = room_index["by_id"].get(str(prev_id), {}).get("exits", {})
        linked = prev_exits.get(direction)
        if linked is not None and (not candidates or linked in candidates):
            return {"status": "resolved", "room_id": linked, "key": key}

    if len(candidates) == 1:
        return {"status": "resolved", "room_id": candidates[0], "key": key}
    if len(candidates) > 1:
        return {"status": "ambiguous", "room_id": None, "candidates": candidates, "key": key}
    return {"status": "unresolved", "room_id": None, "key": key}


def record_batch(commands_and_outputs):
    """The single entry point send_command.py/connect.py call after a
    round of commands. Best-effort: unrecognized output is left alone."""
    player = load_player()
    world = load_world()
    room_index = load_room_index()

    for command, output in commands_and_outputs:
        score = parsers.parse_score(output)
        if score:
            player.update(score)
            player["updated_at"] = _now()

        inventory = parsers.parse_inventory(output)
        if inventory is not None:
            player["inventory"] = inventory
            player["updated_at"] = _now()

        equipment = parsers.parse_equipment(output)
        if equipment is not None:
            player["equipment"] = equipment
            player["updated_at"] = _now()

        room = parsers.parse_room(output)
        if room and room_index:
            resolution = resolve_room(command, room, player, room_index)
            _apply_room_resolution(player, world, room, resolution)
        elif room and not room_index:
            # No index built yet — still record the raw text so nothing
            # is lost, just without an id.
            player["current_room_id"] = None
            player["room_status"] = "no_index"
            player["last_room_seen"] = {"name": room["name"], "desc": room["desc"]}

        exits_only = None if room else parsers.parse_exits(output)
        if exits_only and player.get("current_room_id") is not None:
            room_id = str(player["current_room_id"])
            entry = world["visited"].get(room_id)
            if entry is not None:
                entry.setdefault("live_exits", {}).update(exits_only)
                entry["last_seen"] = _now()

    _write_json(PLAYER_PATH, player)
    _write_json(WORLD_PATH, world)
    _render_player_md(player)
    _render_status_md(player, world, room_index)


def _apply_room_resolution(player, world, room, resolution):
    status = resolution["status"]
    now = _now()
    player["room_status"] = status
    player["last_room_seen"] = {"name": room["name"], "desc": room["desc"]}

    if status == "resolved":
        room_id = str(resolution["room_id"])
        player["current_room_id"] = resolution["room_id"]
        entry = world["visited"].setdefault(room_id, {
            "first_seen": now, "visit_count": 0, "live_exits": {},
        })
        entry["visit_count"] += 1
        entry["last_seen"] = now
        entry["name"] = room["name"]
        if room.get("exits"):
            entry.setdefault("live_exits", {}).update(room["exits"])
    elif status == "ambiguous":
        player["current_room_id"] = None
        key = resolution["key"]
        entry = world["ambiguous"].setdefault(key, {
            "name": room["name"], "desc": room["desc"],
            "candidates": resolution["candidates"], "first_seen": now,
        })
        entry["last_seen"] = now
    else:  # unresolved
        player["current_room_id"] = None
        key = resolution["key"]
        entry = world["unresolved"].setdefault(key, {
            "name": room["name"], "desc": room["desc"],
            "exits": room.get("exits", {}), "first_seen": now,
        })
        entry["last_seen"] = now
        if room.get("exits"):
            entry["exits"].update(room["exits"])


def _fmt_list(items):
    if not items:
        return "empty"
    return ", ".join(items)


def _render_player_md(player):
    lines = ["# Player State", ""]
    if "title" in player:
        lines.append(f"- Name: {player['title']} (level {player.get('level', '?')})")
    if "age" in player:
        lines.append(f"- Age: {player['age']}")
    hp = player.get("hp", {})
    mana = player.get("mana", {})
    move = player.get("movement", {})
    if hp or mana or move:
        lines.append(
            f"- HP: {hp.get('current', '?')}/{hp.get('max', '?')}, "
            f"Mana: {mana.get('current', '?')}/{mana.get('max', '?')}, "
            f"Movement: {move.get('current', '?')}/{move.get('max', '?')}"
        )
    if "ac" in player:
        lines.append(f"- AC: {player['ac']}, Alignment: {player.get('alignment', '?')}")
    if "exp" in player or "exp_needed" in player:
        lines.append(f"- Exp: {player.get('exp', '?')} ({player.get('exp_needed', '?')} needed for next level)")
    if "gold" in player or "quest_points" in player:
        lines.append(f"- Gold: {player.get('gold', '?')}, Quest points: {player.get('quest_points', '?')}")
    if "inventory" in player:
        lines.append(f"- Inventory: {_fmt_list(player['inventory'])}")
    if "equipment" in player:
        lines.append(f"- Equipment: {_fmt_list(player['equipment'])}")

    room_status = player.get("room_status")
    last_room = player.get("last_room_seen", {})
    if room_status == "resolved" and player.get("current_room_id") is not None:
        lines.append(f"- Current location: {last_room.get('name', '?')} (room #{player['current_room_id']})")
    elif room_status == "ambiguous":
        lines.append(f"- Current location: {last_room.get('name', '?')} (ambiguous — matches multiple ground-truth rooms)")
    elif room_status in ("unresolved", "no_index"):
        lines.append(f"- Current location: {last_room.get('name', '?')} (not matched to ground-truth data)")

    lines.append("")
    with open(PLAYER_MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _render_status_md(player, world, room_index):
    lines = ["# Current Status", ""]
    room_id = player.get("current_room_id")
    last_room = player.get("last_room_seen", {})

    if room_id is not None and room_index:
        info = room_index["by_id"].get(str(room_id), {})
        lines.append(f"## {info.get('name', last_room.get('name', '?'))} (id {room_id}, zone {info.get('zone', '?')})")
        lines.append("")
        if last_room.get("desc"):
            lines.append(last_room["desc"])
            lines.append("")
        lines.append("### Exits (ground truth)")
        for direction, target_id in sorted(info.get("exits", {}).items()):
            target_name = room_index["by_id"].get(str(target_id), {}).get("name", "?")
            lines.append(f"- {direction} -> #{target_id} \"{target_name}\"")
        entry = world["visited"].get(str(room_id), {})
        if entry:
            lines.append("")
            lines.append(f"### Observed here: {entry.get('visit_count', 1)} visit(s), last at {_fmt_ts(entry.get('last_seen'))}")
    elif player.get("room_status") == "ambiguous":
        lines.append(f"## {last_room.get('name', '?')} (ambiguous)")
        lines.append("")
        lines.append("This room's text matches more than one ground-truth room and can't be told apart from description alone.")
    elif last_room:
        lines.append(f"## {last_room.get('name', '?')} (unresolved)")
        lines.append("")
        lines.append("No matching room found in ground-truth world data.")
    else:
        lines.append("_No room observed yet._")

    lines.append("")
    with open(STATUS_MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _fmt_ts(ts):
    if not ts:
        return "?"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
