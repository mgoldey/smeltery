# Bench as a desktop app

A [Tauri](https://tauri.app) window that owns a local `smeltery bench` server. One thing to start,
one thing to stop: the app picks a free port, launches the server on loopback, waits for `/healthz`,
and points a WebKit window at it. Closing the window stops the server.

The UI is not bundled here. The window loads whatever the Python server serves, so there is one
copy of the frontend (`src/smeltery/bench/web/`, built from `ui/`) and no second version to drift.

## Running it

The app needs `smeltery` importable by some Python on the machine. It looks for an interpreter in
this order:

1. `$SMELTERY_PYTHON`
2. `.venv/bin/python` next to the repo
3. `python3` on `PATH`

```bash
# from a source checkout
./src-tauri/target/release/smeltery-bench-app

# against a specific interpreter
SMELTERY_PYTHON=/path/to/.venv/bin/python ./src-tauri/target/release/smeltery-bench-app

# a different Bench root (default: ~/.local/share/smeltery/bench)
SMELTERY_BENCH_ROOT=/data/bench ./src-tauri/target/release/smeltery-bench-app
```

If the server cannot start, the window shows what went wrong and the server's own output, rather
than a blank page.

## Building

Needs Rust and the WebKitGTK development packages:

```bash
sudo apt install libwebkit2gtk-4.1-dev libjavascriptcoregtk-4.1-dev \
  libgtk-3-dev libsoup-3.0-dev librsvg2-dev libayatana-appindicator3-dev \
  build-essential curl file
cargo install tauri-cli --version '^2' --locked
```

```bash
cd src-tauri
cargo build --release                  # the binary
cargo tauri build --bundles deb        # Bench_0.1.0_amd64.deb   (~2.8 MiB)
cargo tauri build --bundles appimage   # Bench_0.1.0_amd64.AppImage (~80 MiB)
```

The `.deb` is small because it depends on the system WebKit; the AppImage carries it.

**If this repo's cargo shim is on your `PATH`**, it serializes heavy builds behind ferric's lock.
Take it off the path for these builds:

```bash
export PATH=$(echo "$PATH" | tr ':' '\n' | grep -v ferric-cargo-shim | paste -sd: -)
```

## Notes on the implementation

**Port.** Bound by the OS (`:0`), read back, then handed to the server. There is a microsecond race
where something else could take it; a collision surfaces as a startup failure, not silent breakage.

**Shutdown.** SIGTERM first, so the server runs its own shutdown (with `--stop-tasks-on-exit`, that
cancels queued tasks and terminates running ones). SIGKILL after 10 s if it ignores that.

**Inherited environment.** An AppImage exports `PYTHONHOME`, `PYTHONPATH` and `LD_LIBRARY_PATH`
pointing inside its own mount. A child Python inheriting them dies with `No module named
'encodings'` before reaching smeltery. The first two are cleared outright; for `LD_LIBRARY_PATH`
only the entries under `$APPDIR` are dropped, so a user's own setting survives. This matters
because ferric is a compiled extension and must not link against the bundle's libraries.

**Pipes.** Both stdout and stderr are drained on background threads. An undrained pipe fills and
wedges the server, which would look exactly like a hang.
