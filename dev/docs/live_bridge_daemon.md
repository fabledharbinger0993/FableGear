# FableGear Live Bridge Daemon

The live bridge daemon is the passive-first foundation for decoding CDJ-3000 and Rekordbox link traffic. It is designed to help FableGear become a live instrument layer without pretending to be Rekordbox before the packet model is trustworthy.

## Safety Boundary

- The daemon only listens, records, and summarizes packets.
- It does not spoof a Pioneer device.
- It does not answer CDJs.
- It does not inject packets.
- It does not write to the Rekordbox database.

Keep active impersonation experiments separate from the daemon until a labeled passive capture corpus proves the state model.

## Run

Activate your Python environment first if your shell does not already have the required tools (e.g. the project virtualenv):

```zsh
source /path/to/your/venv/bin/activate
```

Then run the passive daemon from the FableGear repo:

```zsh
python scripts/live_bridge/live_bridge_daemon.py --label idle-baseline
```

The daemon writes:

- `~/.fablegear/live_bridge/events.jsonl`: packet/event stream.
- `~/.fablegear/live_bridge/state.json`: latest summarized state.

UDP-bind mode is passive on the wire, but it is not as non-invasive as a tcpdump capture. Use it for controlled smoke tests and controlled capture sessions until it has been validated on the target rig. If Rekordbox already owns the Pro DJ Link ports, binding can fail or coexist in surprising ways depending on socket options. In that case, use tcpdump-based capture and offline parsing instead of forcing the daemon to bind.

## Decoding Workflow

1. Capture an idle baseline with the CDJ connected.
2. Capture one labeled action at a time: play, pause, cue, browse, load, loop, tempo, jog, filter, mixer-visible changes.
3. Compare packet families, ports, lengths, signatures, and candidate device ID offsets between baseline and action windows.
4. Promote stable observations into the decoder as named fields.
5. Only after stable passive decoding should FableGear expose active live-instrument controls.

Summarize one capture:

```zsh
python scripts/live_bridge/analyze_events.py ~/.fablegear/live_bridge/events.jsonl --pretty
```

Diff a baseline capture against an action capture:

```zsh
python scripts/live_bridge/analyze_events.py baseline.jsonl action-play-pause.jsonl --pretty
```

The diff reports deltas by port, packet family, source, signature, payload length, packet type hint, label, and decoder notes. Those deltas are the working material for promoting hints into named protocol fields.

## Current Decoder Status

The initial decoder recognizes observed packet signatures including `Qspt1WmJOL` and `Mac `, groups traffic by source, destination port, packet family, packet type hint, and tentative device ID offsets. These are grouping hints, not final protocol claims.
