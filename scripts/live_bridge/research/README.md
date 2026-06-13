# ProLink Research — Phase B Reverse-Engineering Tooling

> **Status: research, not product.** Nothing in this directory is wired into
> the FableGear app. These are investigative tools for decoding Pro DJ Link
> traffic, in service of the dual-format export campaign
> (see `docs/dual_format_export.md`, Phase B).

## ⚠️ Network safety — read before running anything here

These scripts fall into **two safety classes**. Know which you're running.

### Observational (safe) — listen only, never transmit

- `prolink_capture_session.py` — wraps `tcpdump`, writes `.pcap`/`.txt`/`.csv`.
  Binds no Pioneer ports, sends no packets.
- `rekordbox_control_sequence_observer.py` — records per-port byte deltas
  while you trigger CDJ actions. Read-only.
- `rekordbox_udp_delta_observer.py` — passive UDP delta summary.

These can run on a live network without announcing FableGear's presence.

### Active (DANGER) — these transmit on the network

- `sandbox_prolink_spoofer.py` — **impersonates a Rekordbox instance**
  (announces itself as a player/collection on the Pro DJ Link network).
- `sandbox_prolink_sniffer.py` — may send discovery/keepalive packets.

**Never run the active tools on a network with live CDJs you care about,
and never at a gig.** A rogue Rekordbox announcement can confuse real
players mid-set — dropped tracks, link errors, a frozen deck in front of a
crowd. Use an isolated test network with sacrificial hardware only. The
`sandbox_` prefix is a warning, not a suggestion.

## captures/

`20260506_053602_cdj_buttons.*` — a real CDJ button-press capture from
2026-05-06 (pcap + tcpdump text + per-packet CSV). **This is irreplaceable
ground-truth data** — it can't be regenerated without setting the hardware
back up. Treat it as a protected artifact. It's the empirical anchor for
decoding which byte patterns correspond to which CDJ control actions.

## How this feeds the export campaign

Phase B needs to understand the on-the-wire and on-disk formats Pioneer
devices use. The observational tools + capture data document the live-link
protocol; that knowledge informs the ANLZ/database format work that
dual-format USB export depends on. Probe first, write later.
