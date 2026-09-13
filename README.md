# Free My VRAM

A tiny watchdog for ComfyUI that gives your GPU back when ComfyUI has been idle for a while.

If you run ComfyUI alongside Ollama, local TTS, training tools, games, or other GPU-heavy apps, ComfyUI may keep models resident in VRAM after a workflow finishes. Free My VRAM watches the ComfyUI queue and history, waits until ComfyUI has been idle for a configurable amount of time, then asks ComfyUI to unload models and free memory through its own API.

No workflow edits. No custom nodes. No CUDA poking.

## What it does

- checks that ComfyUI is reachable
- refuses to unload while jobs are running or queued
- detects when the most recent execution finished
- waits for your configured idle timeout
- calls ComfyUI's `/free` endpoint with `unload_models` and `free_memory`
- can run once from the command line or continuously as a lightweight watchdog
- uses only the Python standard library

## Requirements

- Python 3.9+
- a running ComfyUI instance with its normal HTTP API available

Default ComfyUI URL: `http://127.0.0.1:8188`

## Quick start

Clone the repository and run:

```bash
python free_my_vram.py
```

By default it watches continuously and checks once per minute. Models are eligible to unload after 10 minutes of ComfyUI inactivity.

To test what it would do without actually unloading anything:

```bash
python free_my_vram.py --dry-run --once
```

## Common options

```bash
# unload after 5 minutes idle
python free_my_vram.py --idle-minutes 5

# ComfyUI running on another host or port
python free_my_vram.py --url http://192.168.1.50:8188

# check every 30 seconds
python free_my_vram.py --check-seconds 30

# perform one check and exit (great for cron / Task Scheduler / systemd timers)
python free_my_vram.py --once

# force a memory release now, but still refuse if the queue is busy
python free_my_vram.py --free-now
```

Run `python free_my_vram.py --help` for all options.

## Example output

```text
[12:14:01] ComfyUI idle for 642s (threshold 600s).
[12:14:01] Requesting model unload and memory release...
[12:14:01] Done. ComfyUI accepted the free-memory request.
```

If ComfyUI is busy:

```text
[12:14:01] ComfyUI busy (1 running, 2 pending) - leaving it alone.
```

## Live validation

The standalone script was exercised on 2026-09-13 against:

- ComfyUI 0.34.2
- Python 3.12.3
- Ubuntu 24.04
- NVIDIA GeForce RTX 5090 (32 GB)

A small Krea 2 render left ComfyUI holding **29,820 MiB** of VRAM. Running `--free-now` with an empty queue reduced ComfyUI to **828 MiB** (its bare CUDA context) within five seconds.

A second live render verified the safety check: while ComfyUI reported one running job, `--free-now` refused to unload and printed the busy message instead.

Also verified on that system: dry-run timing, continuous watch mode, no repeated `/free` calls for the same completed job, and clean handling of an unreachable ComfyUI URL.

This is one validated environment, not a claim that every ComfyUI version, OS, or GPU has been tested. Windows runtime testing and broader ComfyUI-version feedback are welcome.

## Linux: run as a systemd service

An example service is included in `examples/free-my-vram.service`.

Edit the paths/user in the file, then:

```bash
sudo cp examples/free-my-vram.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now free-my-vram
```

Check logs with:

```bash
journalctl -u free-my-vram -f
```

## Windows

The simplest setup is to run Free My VRAM in a terminal while ComfyUI is running:

```powershell
py free_my_vram.py
```

For unattended use, create a Windows Task Scheduler entry that starts it at sign-in, or use `--once` on a repeating schedule.

A packaged `.exe` may be added later if there is enough demand. The current version intentionally stays dependency-free and easy to inspect.

## Safety behavior

Free My VRAM is intentionally conservative:

- it will not unload while ComfyUI reports a running or pending job
- network/API failures do not kill ComfyUI; they are logged and retried on the next check
- `--free-now` still respects the queue state
- `--dry-run` never sends the unload request
- after a fresh ComfyUI restart with no usable history, automatic mode skips safely until at least one job has produced history

There is a tiny unavoidable race between reading `/queue` and sending `/free`: a new prompt could arrive in that gap. ComfyUI processes free-memory flags between jobs, so this does not interrupt the active render; the newly completed job may simply be unloaded immediately afterward.

This tool does **not** stop ComfyUI, kill GPU processes, call `nvidia-smi`, modify workflows, or touch CUDA directly.

## Why this exists

This started as a tiny helper inside a larger local AI filmmaking setup where ComfyUI, local LLMs, TTS, and other GPU tools all share one card. ComfyUI retaining a large model between jobs is useful when you are generating repeatedly, but less useful when another local AI workload needs the GPU next.

Free My VRAM exists to make that handoff automatic.

## Status

Early public release. The standalone core behavior has now been directly validated on one real ComfyUI 0.34.2 / Linux / RTX 5090 setup. It is intentionally small. Bug reports and real-world hardware/OS feedback are welcome.

## License

MIT - see [LICENSE](LICENSE).
