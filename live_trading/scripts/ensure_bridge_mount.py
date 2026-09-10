#!/usr/bin/env python3
"""Reconnect the Mac SMB bridge through NetFS without sudo or login dialogs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit


# NetFS.h: NULL mountpath lets NetAuth create the default /Volumes mountpoint.
# UIOption=NoUI prevents an unattended cron job waiting for authentication UI.
# Pass the URL via the environment, never script interpolation or process argv.
_NETFS_SCRIPT = r'''
ObjC.import("Foundation");
ObjC.import("NetFS");
function run() {
    var env = $.NSProcessInfo.processInfo.environment;
    var url = $.NSURL.URLWithString(env.objectForKey("QLIB_MOUNT_URL"));
    var root = ObjC.unwrap(env.objectForKey("QLIB_MOUNT_ROOT"));
    var openOptions = $.NSMutableDictionary.alloc.init;
    openOptions.setObjectForKey($("NoUI"), $("UIOption"));
    var mountOptions = $.NSMutableDictionary.alloc.init;
    var mountPath = null;
    if (root.substring(0, 9) !== "/Volumes/" || root.substring(9).indexOf("/") >= 0) {
        mountPath = $.NSURL.fileURLWithPath($(root));
        mountOptions.setObjectForKey($.NSNumber.numberWithBool(true), $("MountAtMountDir"));
    }
    var mountPoints = Ref();
    var status = $.NetFSMountURLSync(url, mountPath, null, null,
                                   openOptions, mountOptions, mountPoints);
    return JSON.stringify({status: Number(status)});
}
'''


def _bridge_ready(root: Path) -> bool:
    return (
        os.path.ismount(root)
        and (root / "inbox").is_dir()
        and os.access(root / "inbox", os.W_OK)
    )


def _error(message: str) -> int:
    print(f"bridge mount failed: {message}", file=sys.stderr)
    return 1


def ensure_bridge_mount(root: Path, smb_url: str, timeout: int = 45) -> int:
    root = Path(root).expanduser()
    smb_url = "smb:" + smb_url if smb_url.startswith("//") else smb_url
    try:
        parsed = urlsplit(smb_url)
        valid_url = parsed.scheme == "smb" and parsed.hostname and parsed.path.strip("/")
    except ValueError:
        valid_url = False
    if not root.is_absolute() or not valid_url:
        return _error("an absolute bridge directory and SMB server/share URL are required")
    share_name = unquote(parsed.path.strip("/").split("/")[0])
    if root.parent == Path("/Volumes") and root.name.casefold() != share_name.casefold():
        return _error("a /Volumes mountpoint must use the SMB share name; use a user-owned directory for a custom name")
    if _bridge_ready(root):
        print(f"bridge ready: {root}")
        return 0

    try:
        if os.path.ismount(root):
            # Preserve a busy share; never use a forced unmount in a cron job.
            result = subprocess.run(
                ["/sbin/umount", str(root)], capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return _error(f"existing mount at {root} is unusable and could not be unmounted")
        if root.parent != Path("/Volumes"):
            root.mkdir(parents=True, exist_ok=True)

        print(f"connecting bridge via macOS NetFS: {root}", flush=True)
        env = dict(os.environ, QLIB_MOUNT_URL=smb_url, QLIB_MOUNT_ROOT=str(root))
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", _NETFS_SCRIPT],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
        # Another caller may have connected the same volume in the meantime.
        if _bridge_ready(root):
            print(f"bridge mounted: {root}")
            return 0
        if result.returncode != 0:
            return _error(f"NetFS helper exited with status {result.returncode}; no usable mount at {root}")
        status = json.loads(result.stdout)["status"]
        if not isinstance(status, int):
            return _error("NetFS helper returned an invalid status")
        if status != 0:
            return _error(f"NetFS status {status}; check server reachability and saved SMB credentials")
        return _error(f"NetFS returned success but {root}/inbox is not mounted and writable")
    except subprocess.TimeoutExpired:
        return _error("connection or unmount timed out; the scheduler has not started")
    except (OSError, ValueError, KeyError):
        # OS/helper errors can contain the credential-bearing URL. Never echo it.
        return _error("unable to prepare or verify the SMB mount")


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("QLIB_BRIDGE_ROOT", "/Volumes/qmt_bridge")
    url = os.environ.get("QLIB_BRIDGE_SMB_URL", "//qmtshare@192.168.0.110/qmt_bridge")
    return ensure_bridge_mount(Path(root), url)


if __name__ == "__main__":
    sys.exit(main())
