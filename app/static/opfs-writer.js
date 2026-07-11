// Worker ghi PCM stream vào OPFS (client mỏng giữ audio trên thiết bị).
// createSyncAccessHandle chỉ dùng được trong worker. Message được xử lý tuần
// tự qua promise chain — tránh "write" chạy trước khi "start" mở xong file.
let access = null;
let queue = Promise.resolve();

async function handle(msg){
  if (msg.cmd === "start"){
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle("recordings", { create: true });
    const file = await dir.getFileHandle(msg.name, { create: true });
    access = await file.createSyncAccessHandle();
  } else if (msg.cmd === "write"){
    if (access) access.write(new Uint8Array(msg.data), { at: access.getSize() });
  } else if (msg.cmd === "finish"){
    let size = 0;
    if (access){
      access.flush();
      size = access.getSize();
      access.close();
      access = null;
    }
    postMessage({ cmd: "done", size });
  }
}

onmessage = (e) => { queue = queue.then(() => handle(e.data)).catch(() => {}); };
