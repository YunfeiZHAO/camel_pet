mod sidecar;
mod tray;

use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use device_query::{DeviceQuery, DeviceState};
use tauri::Manager;

// Window-local logical regions the cursor should "catch" on.
// Rects are (x, y, w, h). Circles are (cx, cy, radius).
// Logical pixels — the poll thread applies scale + window origin every tick.
#[derive(Default)]
struct PassthroughState {
    rects: Mutex<Vec<(f64, f64, f64, f64)>>,
    circles: Mutex<Vec<(f64, f64, f64)>>,
}

#[tauri::command]
fn set_passthrough_regions(
    state: tauri::State<'_, Arc<PassthroughState>>,
    rects: Vec<[f64; 4]>,
) {
    let mut g = state.rects.lock().expect("passthrough rects poisoned");
    *g = rects.into_iter().map(|r| (r[0], r[1], r[2], r[3])).collect();
}

#[tauri::command]
fn set_passthrough_circles(
    state: tauri::State<'_, Arc<PassthroughState>>,
    circles: Vec<[f64; 3]>,
) {
    let mut g = state.circles.lock().expect("passthrough circles poisoned");
    *g = circles.into_iter().map(|c| (c[0], c[1], c[2])).collect();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let passthrough = Arc::new(PassthroughState::default());

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(passthrough.clone())
        .invoke_handler(tauri::generate_handler![
            set_passthrough_regions,
            set_passthrough_circles
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            sidecar::spawn(&handle);
            if let Err(e) = tray::setup(&handle) {
                log::warn!("tray setup failed: {}", e);
            }

            let window = match app.get_webview_window("pet") {
                Some(w) => w,
                None => {
                    log::warn!("pet window not found; click-through disabled");
                    return Ok(());
                }
            };

            // Cover the full primary monitor. Size comes from the system, not
            // from hard-coded config. Done here (before the window is shown) so
            // the user never sees a tiny pre-resize flash.
            match window.primary_monitor() {
                Ok(Some(mon)) => {
                    let _ = window.set_position(*mon.position());
                    let _ = window.set_size(*mon.size());
                }
                Ok(None) => log::warn!("no primary monitor detected"),
                Err(e) => log::warn!("primary_monitor() failed: {}", e),
            }
            let _ = window.show();

            // Start passthrough ON — the whole transparent window lets clicks
            // fall through to apps underneath until JS registers rects.
            let _ = window.set_ignore_cursor_events(true);

            let state_for_thread = passthrough.clone();
            let window_for_thread = window.clone();
            thread::spawn(move || {
                let device = DeviceState::new();
                let mut current_passthrough = true;
                loop {
                    thread::sleep(Duration::from_millis(40));

                    let rects = {
                        let g = match state_for_thread.rects.lock() {
                            Ok(g) => g,
                            Err(_) => continue,
                        };
                        g.clone()
                    };
                    let circles = {
                        let g = match state_for_thread.circles.lock() {
                            Ok(g) => g,
                            Err(_) => continue,
                        };
                        g.clone()
                    };

                    let mouse = device.get_mouse();
                    let (mx, my) = (mouse.coords.0 as f64, mouse.coords.1 as f64);

                    let win_pos = match window_for_thread.outer_position() {
                        Ok(p) => p,
                        Err(_) => continue,
                    };
                    let scale = window_for_thread.scale_factor().unwrap_or(1.0);

                    let inside_rects = rects.iter().any(|(x, y, w, h)| {
                        let rx = win_pos.x as f64 + x * scale;
                        let ry = win_pos.y as f64 + y * scale;
                        let rw = w * scale;
                        let rh = h * scale;
                        mx >= rx && mx <= rx + rw && my >= ry && my <= ry + rh
                    });
                    let inside_circles = circles.iter().any(|(cx, cy, r)| {
                        let ccx = win_pos.x as f64 + cx * scale;
                        let ccy = win_pos.y as f64 + cy * scale;
                        let cr = r * scale;
                        let dx = mx - ccx;
                        let dy = my - ccy;
                        dx * dx + dy * dy <= cr * cr
                    });
                    let inside = inside_rects || inside_circles;

                    let desired = !inside;
                    if desired != current_passthrough {
                        if window_for_thread.set_ignore_cursor_events(desired).is_ok() {
                            current_passthrough = desired;
                        }
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Don't exit — hide to tray instead.
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Camel Pet");
}
