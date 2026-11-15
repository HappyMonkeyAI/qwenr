// Only the settings window may drive IPC. Wrap every ipcMain.handle handler.
export function guardIpc(getWindow, handler) {
  return async (event, ...args) => {
    const win = getWindow();
    if (!win || win.isDestroyed?.() || event?.sender !== win.webContents) {
      throw new Error("untrusted renderer");
    }
    return handler(event, ...args);
  };
}
