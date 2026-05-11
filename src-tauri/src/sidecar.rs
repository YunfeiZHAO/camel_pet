use std::sync::Mutex;

use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

pub struct SidecarHandle(pub Mutex<Option<CommandChild>>);

/// Spawn the bundled `camel-agent` sidecar. In dev mode (where the binary
/// hasn't been packaged yet) this logs a warning and returns — the user is
/// expected to run the Python server manually on port 8765.
pub fn spawn(app: &AppHandle) {
    let shell = app.shell();
    let cmd = match shell.sidecar("camel-agent") {
        Ok(c) => c,
        Err(e) => {
            log::warn!(
                "sidecar not found ({}). Start it manually with `poetry run camel-agent`.",
                e
            );
            return;
        }
    };

    match cmd.spawn() {
        Ok((mut rx, child)) => {
            if let Some(handle) = app.try_state::<SidecarHandle>() {
                *handle.0.lock().unwrap() = Some(child);
            } else {
                app.manage(SidecarHandle(Mutex::new(Some(child))));
            }

            tauri::async_runtime::spawn(async move {
                while let Some(ev) = rx.recv().await {
                    match ev {
                        CommandEvent::Stdout(line) => {
                            log::info!("[agent] {}", String::from_utf8_lossy(&line).trim_end())
                        }
                        CommandEvent::Stderr(line) => {
                            log::info!("[agent:err] {}", String::from_utf8_lossy(&line).trim_end())
                        }
                        CommandEvent::Terminated(p) => {
                            log::warn!("[agent] exited code={:?}", p.code);
                            break;
                        }
                        _ => {}
                    }
                }
            });
        }
        Err(e) => log::error!("failed to spawn sidecar: {}", e),
    }
}

pub fn kill(app: &AppHandle) {
    if let Some(h) = app.try_state::<SidecarHandle>() {
        if let Some(child) = h.0.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
}
