#!/usr/bin/env python3
"""Build a slim room-lookup cache from the ground-truth world data.

week0_explore/preview/web/public/data/rooms.json is parsed from the exact
world files Docker-mounts into the running CircleMUD container
(week0_explore/infrastructure/lib/world/), so it is authoritative for the
currently-running world: every room already has a stable numeric id (vnum),
zone, name, description, and fully-resolved exits.

That file is ~9.5MB, too big to reparse on every send_command.py/connect.py
invocation. This script builds state/room_index.json once, with two lookup
directions:

  - "by_id": {id: {name, zone, exits}} — for resolving movement. If the
    previous room is known and the player just moved (e.g. "north"), the
    next room id is read directly off the previous room's exits: exact,
    no ambiguity.
  - "by_hash": {normalized-name+desc hash: [id, ...]} — for resolving
    position from scratch (e.g. right after login) by matching the room
    text CircleMUD just printed. Several zones (mazes in particular) reuse
    identical room text across many distinct rooms on purpose, so a hash
    can map to more than one id — those are genuinely ambiguous without a
    known previous position, and callers must treat a multi-id match as
    "candidates", not a single answer.

Re-run this script if the mounted world changes (e.g. after
`docker compose down -v`).

Usage:
  python3 room_index.py [path/to/rooms.json]
"""
import hashlib
import json
import os
import re
import sys

DEFAULT_ROOMS_JSON = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "week0_explore", "preview", "web", "public", "data", "rooms.json",
)
STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")
OUTPUT_PATH = os.path.join(STATE_DIR, "room_index.json")

DIR_NAMES = {0: "north", 1: "east", 2: "south", 3: "west", 4: "up", 5: "down"}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def normalize(text: str) -> str:
    text = ANSI_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def content_key(name: str, desc: str) -> str:
    digest = hashlib.sha256(f"{normalize(name)}\n{normalize(desc)}".encode("utf-8")).hexdigest()
    return digest


def build(rooms_json_path: str) -> dict:
    with open(rooms_json_path, "r", encoding="utf-8") as f:
        rooms = json.load(f)

    by_id = {}
    by_hash = {}
    for room_id, room in rooms.items():
        rid = int(room_id)
        name = room.get("name", "")
        desc = room.get("desc", "")
        exits = {
            DIR_NAMES.get(e["dir"], str(e["dir"])): e["room_linked"]
            for e in room.get("exits", [])
        }
        by_id[room_id] = {"name": name, "zone": room.get("zone_number"), "exits": exits}
        key = content_key(name, desc)
        by_hash.setdefault(key, []).append(rid)

    return {"by_id": by_id, "by_hash": by_hash}


def main():
    rooms_json_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOMS_JSON
    rooms_json_path = os.path.abspath(rooms_json_path)
    if not os.path.exists(rooms_json_path):
        print(f"error: rooms.json not found at {rooms_json_path}", file=sys.stderr)
        print("run week0_explore/bin/convert-world first, or pass a path explicitly", file=sys.stderr)
        sys.exit(1)

    index = build(rooms_json_path)

    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = OUTPUT_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(index, f)
    os.replace(tmp_path, OUTPUT_PATH)

    dupes = sum(1 for ids in index["by_hash"].values() if len(ids) > 1)
    print(
        f"indexed {len(index['by_id'])} rooms ({dupes} ambiguous text collisions) "
        f"-> {os.path.abspath(OUTPUT_PATH)}"
    )


if __name__ == "__main__":
    main()
