// Bench as a desktop window.
//
// This process owns a `smeltery bench` server: it picks a free port, starts the server bound to
// loopback, waits until /healthz answers, then points a webview at it. When the window closes the
// server is killed, so there is one thing to start and one thing to stop.
//
// Why a child process and not a bundled sidecar binary: smeltery is a Python package that the user
// already has (ferric wheels, prebuilt pipelines, their own Bench root). Bundling a second copy
// would mean two versions of the app on one machine and a far larger artifact. The trade is that
// this app needs smeltery importable -- which is checked, and reported in the window rather than as
// a silent blank page.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Read};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{RunEvent, WebviewUrl, WebviewWindowBuilder};

/// How long to wait for the server to answer /healthz before giving up.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(90);
/// How often to poll /healthz while waiting.
const POLL_INTERVAL: Duration = Duration::from_millis(150);

/// A port the OS told us is free.
///
/// There is an unavoidable race here: we bind a port to learn its number, drop the listener, then
/// hand the number to the server. Something else could take it in between. Binding loopback only
/// and handing the port straight over keeps that window to microseconds, and a collision surfaces
/// as a startup failure rather than silent breakage.
fn free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))?;
    Ok(listener.local_addr()?.port())
}

/// The server process plus everything needed to report on it if it dies.
struct Server {
    child: Child,
    port: u16,
    /// Captured stderr, so a crash can be shown to the user instead of a blank window.
    log: Arc<Mutex<Vec<String>>>,
}

impl Server {
    /// Start `smeltery bench` on `port`, reading its stderr into `log` on a background thread.
    fn spawn(python: &str, root: Option<&str>, port: u16) -> std::io::Result<Self> {
        let mut cmd = Command::new(python);
        // An AppImage exports PYTHONHOME/PYTHONPATH pointing inside its own mount, and a child
        // Python that inherits them looks for its stdlib there, failing with "No module named
        // 'encodings'" before it ever reaches smeltery. The interpreter we launch must find its
        // own installation, so these are cleared. (Also covers a user's stray PYTHONHOME.)
        cmd.env_remove("PYTHONHOME")
            .env_remove("PYTHONPATH")
            .env_remove("PYTHONSTARTUP");
        // LD_LIBRARY_PATH is also redirected into the mount. Dropping it wholesale would break a
        // user who set it for their own reasons, so remove only the AppImage's own entries: the
        // ones under $APPDIR. ferric is a compiled extension, so loading it against the bundle's
        // libraries instead of the system's is a real hazard, not a hypothetical one.
        if let Ok(appdir) = std::env::var("APPDIR") {
            if let Ok(libpath) = std::env::var("LD_LIBRARY_PATH") {
                let kept: Vec<&str> = libpath
                    .split(':')
                    .filter(|p| !p.is_empty() && !p.starts_with(&appdir))
                    .collect();
                if kept.is_empty() {
                    cmd.env_remove("LD_LIBRARY_PATH");
                } else {
                    cmd.env("LD_LIBRARY_PATH", kept.join(":"));
                }
            }
        }
        cmd.arg("-m").arg("smeltery.cli").arg("bench");
        if let Some(root) = root {
            cmd.arg(root);
        }
        cmd.arg("--port")
            .arg(port.to_string())
            .arg("--host")
            .arg("127.0.0.1")
            // A desktop window closing should not leave orphaned quantum-chemistry jobs behind.
            .arg("--stop-tasks-on-exit")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = cmd.spawn()?;
        let log = Arc::new(Mutex::new(Vec::new()));

        // Drain both pipes. If nobody reads them, a chatty server eventually blocks on a full pipe
        // buffer and wedges -- which would look exactly like a hang.
        for pipe in [
            child.stderr.take().map(|p| Box::new(p) as Box<dyn Read + Send>),
            child.stdout.take().map(|p| Box::new(p) as Box<dyn Read + Send>),
        ]
        .into_iter()
        .flatten()
        {
            let log = Arc::clone(&log);
            std::thread::spawn(move || {
                for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                    eprintln!("[bench] {line}");
                    let mut log = log.lock().unwrap();
                    // Bounded: a long-running server must not grow this without limit.
                    if log.len() >= 200 {
                        log.remove(0);
                    }
                    log.push(line);
                }
            });
        }

        Ok(Server { child, port, log })
    }

    /// Block until /healthz answers, the process dies, or the timeout expires.
    fn wait_until_healthy(&mut self) -> Result<(), String> {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, self.port);

        while Instant::now() < deadline {
            // If it has already exited, waiting the full timeout is pointless.
            match self.child.try_wait() {
                Ok(Some(status)) => {
                    return Err(format!(
                        "the server exited before it was ready ({status}).\n\n{}",
                        self.recent_log()
                    ))
                }
                Ok(None) => {}
                Err(e) => return Err(format!("could not check on the server process: {e}")),
            }

            if health_ok(addr) {
                return Ok(());
            }
            std::thread::sleep(POLL_INTERVAL);
        }

        Err(format!(
            "the server did not answer /healthz within {}s.\n\n{}",
            STARTUP_TIMEOUT.as_secs(),
            self.recent_log()
        ))
    }

    fn recent_log(&self) -> String {
        let log = self.log.lock().unwrap();
        if log.is_empty() {
            "It printed nothing.".to_string()
        } else {
            let tail: Vec<_> = log.iter().rev().take(15).rev().cloned().collect();
            tail.join("\n")
        }
    }

    /// Ask the server to stop, then make sure it did.
    ///
    /// SIGTERM lets it run its own shutdown (which, with --stop-tasks-on-exit, stops queued and
    /// running tasks). SIGKILL is the fallback for a server wedged badly enough to ignore it.
    fn shutdown(&mut self) {
        if matches!(self.child.try_wait(), Ok(Some(_))) {
            return;
        }

        #[cfg(unix)]
        {
            // SAFETY: kill(2) with a pid we own and a valid signal number.
            unsafe {
                libc_kill(self.child.id() as i32, 15);
            }
            let deadline = Instant::now() + Duration::from_secs(10);
            while Instant::now() < deadline {
                if matches!(self.child.try_wait(), Ok(Some(_))) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            eprintln!("[bench] server ignored SIGTERM for 10s; killing it");
        }

        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(unix)]
extern "C" {
    #[link_name = "kill"]
    fn libc_kill(pid: i32, sig: i32) -> i32;
}

/// One GET /healthz. Any non-200, or any I/O error, counts as not-yet-ready.
fn health_ok(addr: SocketAddrV4) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&addr.into(), Duration::from_millis(500)) else {
        return false;
    };
    if stream.set_read_timeout(Some(Duration::from_millis(1500))).is_err() {
        return false;
    }
    use std::io::Write;
    let req = format!("GET /healthz HTTP/1.0\r\nHost: 127.0.0.1:{}\r\n\r\n", addr.port());
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut body = String::new();
    if stream.read_to_string(&mut body).is_err() {
        return false;
    }
    body.starts_with("HTTP/1.0 200") || body.starts_with("HTTP/1.1 200")
}

/// The interpreter to run. A venv next to the repo is the common case during development;
/// SMELTERY_PYTHON overrides it; otherwise fall back to whatever `python3` is on PATH.
fn python_command() -> String {
    if let Ok(p) = std::env::var("SMELTERY_PYTHON") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    for candidate in [
        concat!(env!("CARGO_MANIFEST_DIR"), "/../../.venv/bin/python"),
        concat!(env!("CARGO_MANIFEST_DIR"), "/../../../.venv/bin/python"),
    ] {
        if std::path::Path::new(candidate).is_file() {
            return candidate.to_string();
        }
    }
    "python3".to_string()
}

/// Shown in the window when the server could not be started. Plain HTML: at this point there is no
/// server to serve anything, so this is a data: URL.
fn error_page(detail: &str) -> String {
    // Minimal escaping -- this text is our own message plus server output, but it lands in HTML.
    let escaped = detail
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    format!(
        r#"<!doctype html><html><head><meta charset="utf-8">
<title>Bench did not start</title><style>
:root {{ color-scheme: light dark; }}
body {{ font: 14px/1.55 system-ui, sans-serif; margin: 0; padding: 2.5rem 2rem;
        background: #fbfbfa; color: #1a1a19; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #16161a; color: #e8e8e6; }} }}
h1 {{ font-size: 1.15rem; margin: 0 0 .5rem; }}
p {{ max-width: 60ch; color: #55554f; }}
@media (prefers-color-scheme: dark) {{ p {{ color: #a3a39c; }} }}
pre {{ background: #00000010; padding: .9rem 1rem; border-radius: 6px; overflow: auto;
       font-size: 12.5px; max-height: 50vh; white-space: pre-wrap; }}
code {{ background: #00000012; padding: .1rem .35rem; border-radius: 3px; }}
</style></head><body>
<h1>Bench did not start</h1>
<p>This window runs <code>smeltery bench</code> for you. That server did not come up, so there is
nothing to show. The server's own output is below.</p>
<pre>{escaped}</pre>
<p>Most often this means smeltery is not installed for the interpreter being used. Set
<code>SMELTERY_PYTHON</code> to the right one, for example
<code>SMELTERY_PYTHON=/path/to/.venv/bin/python</code>.</p>
</body></html>"#
    )
}

fn main() {
    let python = python_command();
    let root = std::env::var("SMELTERY_BENCH_ROOT").ok();

    // Start the server before the window, so the window opens on something real.
    let started: Result<Server, String> = free_port()
        .map_err(|e| format!("could not find a free port: {e}"))
        .and_then(|port| {
            Server::spawn(&python, root.as_deref(), port).map_err(|e| {
                format!(
                    "could not start the server with `{python} -m smeltery.cli bench`: {e}\n\n\
                     Is smeltery installed for that interpreter?"
                )
            })
        })
        .and_then(|mut server| match server.wait_until_healthy() {
            Ok(()) => Ok(server),
            Err(e) => {
                server.shutdown();
                Err(e)
            }
        });

    let mut error_page_path: Option<std::path::PathBuf> = None;
    let (url, server) = match started {
        Ok(server) => (
            WebviewUrl::External(
                format!("http://127.0.0.1:{}/", server.port)
                    .parse()
                    .expect("a loopback URL built from a u16 port is always valid"),
            ),
            Some(server),
        ),
        Err(detail) => {
            eprintln!("[bench] {detail}");
            // Written to a file and loaded over file://, not a data: URL -- Tauri rejects those
            // unless the `webview-data-url` feature is on, and failing to show the diagnosis is
            // precisely the blank window this page exists to prevent.
            let path = std::env::temp_dir().join(format!("bench-startup-{}.html", std::process::id()));
            let url = match std::fs::write(&path, error_page(&detail)) {
                Ok(()) => {
                    error_page_path = Some(path.clone());
                    format!("file://{}", path.display())
                }
                // Nothing left to show it in; the message is already on stderr.
                Err(e) => {
                    eprintln!("[bench] could not write the error page: {e}");
                    std::process::exit(1);
                }
            };
            (
                WebviewUrl::External(url.parse().expect("a file:// URL from a temp path is valid")),
                None,
            )
        }
    };

    // The server handle lives in Tauri's state so the exit hook can stop it.
    let server = Arc::new(Mutex::new(server));
    let on_exit = Arc::clone(&server);
    // The error page, if one was written, is removed on the way out.
    let scratch = Arc::new(Mutex::new(error_page_path));

    tauri::Builder::default()
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", url.clone())
                .title("Bench")
                .inner_size(1440.0, 920.0)
                .min_inner_size(900.0, 600.0)
                .resizable(true)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Bench window")
        .run(move |_app, event| {
            // Covers both the last window closing and the process being asked to exit.
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(mut s) = on_exit.lock().unwrap().take() {
                    s.shutdown();
                }
                if let Some(path) = scratch.lock().unwrap().take() {
                    let _ = std::fs::remove_file(path);
                }
            }
        });
}
