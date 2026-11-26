//! This process's windows on the X server, by the window manager's client
//! list (`_NET_CLIENT_LIST`) and each client's `_NET_WM_PID`.

use x11rb::connection::Connection;
use x11rb::protocol::xproto::{AtomEnum, ConnectionExt as _, Window};

/// The atom named `name`, interned.
pub fn atom(conn: &impl Connection, name: &str) -> Result<u32, String> {
    let reply = conn.intern_atom(false, name.as_bytes()).map_err(|e| e.to_string())?.reply().map_err(|e| e.to_string())?;
    Ok(reply.atom)
}

/// The 32-bit values of `window`'s property `name` (empty when unset).
pub fn property32(conn: &impl Connection, window: Window, name: u32, kind: AtomEnum) -> Result<Vec<u32>, String> {
    let reply = conn.get_property(false, window, name, kind, 0, u32::MAX).map_err(|e| e.to_string())?.reply().map_err(|e| e.to_string())?;
    Ok(reply.value32().map(|v| v.collect()).unwrap_or_default())
}

/// The managed client windows of process `pid` on the X server, by the
/// window manager's client list.
pub fn windows_of(conn: &impl Connection, root: Window, pid: u32) -> Result<Vec<Window>, String> {
    let (clients, wm_pid) = (atom(conn, "_NET_CLIENT_LIST")?, atom(conn, "_NET_WM_PID")?);
    let mut found = Vec::new();
    for window in property32(conn, root, clients, AtomEnum::WINDOW)? {
        if property32(conn, window, wm_pid, AtomEnum::CARDINAL)?.first() == Some(&pid) {
            found.push(window);
        }
    }
    Ok(found)
}
