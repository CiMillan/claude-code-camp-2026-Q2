---
name: play-mud
description: Connect to and play the local CircleMUD server — send commands, check character status, move around the game world, chat. Use when the user wants to play, explore, or interact with the MUD, log in a character, or run MUD commands.
---

# Play MUD

Connects to the CircleMUD server running locally (started via `week0_explore/infrastructure/docker-compose.yml`, see `week0_explore/HOW_TO_PLAY.md`) and plays it — either an interactive session or one-shot commands.

## Credentials

Never store a username or password in this file or in any tracked script. Read them from the environment instead:

- `MUD_NAME` — character name
- `MUD_PASSWORD` — character password
- `MUD_HOST` — default `localhost`
- `MUD_PORT` — default `4000`

Export them in your shell, or put them in a project-root `.env` (already gitignored) and `source .env` before running a script. If `MUD_NAME`/`MUD_PASSWORD` are unset, `scripts/send_command.py` refuses to run rather than guessing or prompting on stdin; `scripts/connect.py` falls back to showing the raw connect banner without logging in.

## Connecting

1. Confirm the server is reachable: `nc -z localhost 4000` (or whatever `MUD_PORT` is set to). If it refuses, start it per `week0_explore/HOW_TO_PLAY.md` (`docker compose up --build` from `week0_explore/infrastructure`) and retry. Done when the port accepts a connection.
2. For a live back-and-forth session, run `python3 scripts/connect.py`. It logs in with the env credentials (or drops you at the raw connect banner if unset) and relays stdin/stdout until the connection closes or you hit Ctrl-D.
3. For a single command from an agent loop (no interactive terminal needed), run `python3 scripts/send_command.py <command> [<command2> ...]`, e.g. `python3 scripts/send_command.py look score`. Each argument is sent as one line; output prints in the order sent. Done when every command's response has printed and the process has exited.

## Memory (player + world state)

Every invocation of `connect.py`/`send_command.py` is a fresh process, so continuity lives in `state/` (gitignored — it's live session data, not something to commit or hand-edit):

- `state/player.json` / `state/player.md` — current stats, inventory, equipment, current room. The `.md` is a generated view for skimming, not the record.
- `state/world.json` / `state/status.md` — which rooms have actually been visited, plus a status view of the current room and its exits.
- `state/room_index.json` — a cache built by `scripts/room_index.py` from `week0_explore/preview/web/public/data/rooms.json`, the **ground-truth** world data parsed from the exact `.wld` files Docker-mounts into the running server. That file already has every room's stable id, full connectivity graph, and zone for the currently-running world — run `python3 scripts/room_index.py` once (or again after `docker compose down -v` / a world edit) to build the cache.

Recording is automatic and best-effort: `send_command.py`/`connect.py` run every command's output through `scripts/parsers.py` after sending it, and silently ignore anything unrecognized (combat spam, chat, NPC dialogue). Don't add a `--record` flag or similar — this happens on every existing invocation with no new syntax.

**Why JSON is the source of truth and Markdown is only a view:** an earlier prototype (`week0_explore/explore_architecture/001_playing_agent/data/world.md`) freehand-maintained a prose map, and with only ~7 rooms recorded it already had three ambiguous "Main Street" entries disambiguated only by wording. Since the full room graph already exists as ground truth, world tracking here is a small "visited-room" overlay resolved against it (by movement first — if the previous room and direction are known, the ground-truth exit is exact and needs no text matching — falling back to matching the room's own text against `room_index.json` only when position is unknown, e.g. right after login). A handful of zones (mazes in particular) intentionally reuse identical room text across many rooms, so a text match can be genuinely ambiguous; those are recorded under `world.json`'s `"ambiguous"` key with their candidate ids rather than guessed at. This also answers the "does Markdown scale" question: nothing that scales with world size is stored as prose — only ids, which stay cheap regardless of how large the world is.

## Notes

- The scripts are pure-Python (stdlib `socket` only) — no gem/bundle install needed. The repo also has a Ruby `mud_manager` gem (`week0_explore/mud_manager`) with equivalent session/login logic, but it requires Ruby ≥3.0 (endless-method syntax) and this machine only has Ruby 2.6.10, so it can't run here. Use these scripts unless the Ruby toolchain gets upgraded.
- CircleMUD's login dance is: name prompt → password prompt → "Welcome"/"Reconnecting"/"Wrong password". `scripts/mud_session.py` walks this and raises `LoginError` on a bad password rather than hanging.
- The first character ever created on a fresh world becomes the admin/Implementor (see `HOW_TO_PLAY.md`). Don't create a brand-new character through these scripts against a shared or persistent world without knowing that.
- Room-name detection in `scripts/parsers.py` relies on CircleMUD color-coding the room name as `\x1b[0;33m<name>\x1b[0m` — this assumes ANSI color is on (the default; nothing here disables it). Without it, room text can't be told apart from ordinary command output and won't be recorded.
