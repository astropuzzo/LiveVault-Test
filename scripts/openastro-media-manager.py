#!/usr/bin/env python3
"""OpenAstro removable-media manager.

Only USB filesystems marked removable by the kernel are eligible. LiveVault's
SERVER and SHARE UUIDs are permanently excluded. Media is mounted read/write
under /srv/openastro-media for authenticated OpenAstro imports; DLNA and guest
SMB access remain read-only so normal playback clients cannot modify files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import time

MEDIA_ROOT = Path("/srv/openastro-media")
RECONCILE_STATE = Path("/run/openastro-media-reconcile-state.json")
MOUNTINFO = Path("/proc/1/mountinfo")
UUID_DIR = Path("/dev/disk/by-uuid")
EXCLUDED_UUIDS = {
    "5fe2d0f6-b485-44e9-8e26-31fb0d217db2",
    "7EBD-F531",
    "92cd9d54-37a0-48a2-8de9-a128233abfb5",
    "15f4c6be-1102-4331-9904-f78e78afd1fd",
    "B2F0-82D2",
}
SUPPORTED = {"exfat", "vfat", "fat", "fat32", "ntfs", "ntfs3", "ext2", "ext3", "ext4"}
UUID_RE = re.compile(r"^[A-Za-z0-9._:-]{2,128}$")


def run(args: list[str], *, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=timeout)


def lsblk_tree() -> list[dict]:
    result = run(["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,TRAN,RM,HOTPLUG,SIZE,FSTYPE,LABEL,UUID,PKNAME,MOUNTPOINTS,MODEL"])
    return json.loads(result.stdout).get("blockdevices", [])


def walk(nodes: list[dict], parent: dict | None = None):
    for node in nodes:
        yield node, parent
        yield from walk(node.get("children") or [], node)


def disk_ancestor(node: dict, parent: dict | None, nodes: list[dict]) -> dict:
    if node.get("type") == "disk":
        return node
    if parent and parent.get("type") == "disk":
        return parent
    pkname = node.get("pkname")
    if pkname:
        for candidate, _ in walk(nodes):
            if candidate.get("name") == pkname:
                return candidate
    return parent or node


def safe_name(label: str | None, uuid: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", (label or "USB").strip()).strip("-._") or "USB"
    token = re.sub(r"[^A-Za-z0-9]", "", uuid)[-8:] or "MEDIA"
    return f"{base[:40]}-{token}"


def mountpoint_for(info: dict) -> Path:
    return MEDIA_ROOT / safe_name(info.get("label"), info["uuid"])


def discover() -> list[dict]:
    nodes = lsblk_tree()
    found: list[dict] = []
    for node, parent in walk(nodes):
        uuid = str(node.get("uuid") or "")
        fstype = str(node.get("fstype") or "").lower()
        if not uuid or uuid in EXCLUDED_UUIDS or fstype not in SUPPORTED:
            continue
        if node.get("type") not in {"part", "disk"}:
            continue
        disk = disk_ancestor(node, parent, nodes)
        removable = bool(int(node.get("rm") or 0)) or bool(int(disk.get("rm") or 0))
        if str(disk.get("tran") or "").lower() != "usb" or not removable:
            continue
        info = {
            "uuid": uuid,
            "label": str(node.get("label") or "USB"),
            "fstype": fstype,
            "device": str(node.get("path") or f"/dev/{node.get('name')}"),
            "disk": str(disk.get("path") or f"/dev/{disk.get('name')}"),
            "disk_name": str(disk.get("name") or ""),
            "model": str(disk.get("model") or node.get("model") or "USB storage").strip(),
            "size": int(node.get("size") or 0),
            "mountpoint": str(mountpoint_for({"label": node.get("label"), "uuid": uuid})),
            "existing_mounts": [str(x) for x in (node.get("mountpoints") or []) if x],
        }
        found.append(info)
    return sorted(found, key=lambda item: (item["label"].lower(), item["uuid"]))


def get_info(uuid: str) -> dict:
    if not UUID_RE.fullmatch(uuid) or uuid in EXCLUDED_UUIDS:
        raise RuntimeError("Supporto media non valido")
    for item in discover():
        if item["uuid"] == uuid:
            return item
    raise RuntimeError("Supporto rimovibile non presente o non consentito")


def _mount_unescape(value: str) -> str:
    for encoded, literal in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(encoded, literal)
    return value


def mount_table() -> dict[str, dict]:
    """Read the host mount namespace once; normal reconciliation needs no findmnt forks."""
    rows: dict[str, dict] = {}
    try:
        lines = MOUNTINFO.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        fields = left.split(); tail = right.split()
        if len(fields) < 6 or len(tail) < 3:
            continue
        target = _mount_unescape(fields[4])
        rows[target] = {
            "source": _mount_unescape(tail[1]),
            "options": set(fields[5].split(',')) | set(tail[2].split(',')),
            "major_minor": fields[2],
        }
    return rows


def source_for_target(target: Path, mounts: dict[str, dict] | None = None) -> str | None:
    row = (mounts or mount_table()).get(str(target))
    return str(row.get("source")) if row else None


def _astro_ids() -> tuple[int, int]:
    try:
        user = pwd.getpwnam("astro")
        return user.pw_uid, user.pw_gid
    except KeyError:
        return 1001, 1001


def mount_options(info: dict) -> str:
    common = ["rw", "nosuid", "nodev", "noexec"]
    fs = info["fstype"]
    uid, gid = _astro_ids()
    if fs in {"exfat", "vfat", "fat", "fat32"}:
        common += [f"uid={uid}", f"gid={gid}", "fmask=0133", "dmask=0022"]
    elif fs in {"ntfs", "ntfs3"}:
        common += [f"uid={uid}", f"gid={gid}", "umask=022"]
    return ",".join(common)


def restart_indexer() -> None:
    subprocess.run(["systemctl", "try-restart", "minidlna.service"], check=False, capture_output=True)


def mount_media(uuid: str, *, quiet: bool = False, info: dict | None = None) -> dict:
    info = info or get_info(uuid)
    if str(info.get("uuid") or "") != uuid:
        raise RuntimeError("Identità supporto media incoerente")
    target = Path(info["mountpoint"])
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    target.mkdir(parents=True, exist_ok=True, mode=0o755)
    mounts = mount_table()
    current = source_for_target(target, mounts)
    real_device = str(Path(f"/dev/disk/by-uuid/{uuid}").resolve())
    if current:
        if Path(current).resolve() != Path(real_device).resolve():
            raise RuntimeError(f"Mountpoint già usato da {current}")
        # Upgrade an older read-only Media Center mount in place.
        options = set((mounts.get(str(target)) or {}).get("options") or [])
        if "ro" in options:
            run(["mount", "-o", "remount,rw", str(target)])
        return info
    if info["existing_mounts"]:
        raise RuntimeError(f"Supporto già montato altrove: {', '.join(info['existing_mounts'])}")
    run(["mount", "-o", mount_options(info), f"/dev/disk/by-uuid/{uuid}", str(target)])
    current = source_for_target(target)
    if not current or Path(current).resolve() != Path(real_device).resolve():
        subprocess.run(["umount", str(target)], check=False)
        raise RuntimeError("Verifica mount media fallita")
    if info["fstype"] in {"ext2", "ext3", "ext4"}:
        uid, gid = _astro_ids()
        try:
            os.chown(target, uid, gid)
            os.chmod(target, target.stat().st_mode | 0o700)
        except OSError as exc:
            subprocess.run(["umount", str(target)], check=False)
            raise RuntimeError(f"Impossibile rendere scrivibile il supporto ext: {exc}") from exc
    restart_indexer()
    if not quiet:
        print(f"{info['label']} montata read/write in {target}; guest/DLNA restano read-only")
    return info


def removable_disk_safe_to_delete(info: dict) -> bool:
    try:
        result = run(["lsblk", "-J", "-o", "PATH,TYPE,RM,MOUNTPOINTS,UUID", info["disk"]])
        root = (json.loads(result.stdout).get("blockdevices") or [])[0]
    except Exception:
        return False
    if not bool(int(root.get("rm") or 0)):
        return False
    for node, _ in walk([root]):
        uuid = str(node.get("uuid") or "")
        for mount in node.get("mountpoints") or []:
            if mount and not str(mount).startswith(str(MEDIA_ROOT) + "/"):
                return False
        if uuid in EXCLUDED_UUIDS:
            return False
    return True


def eject_media(uuid: str) -> None:
    info = get_info(uuid)
    target = Path(info["mountpoint"])
    subprocess.run(["systemctl", "stop", "minidlna.service"], check=False, capture_output=True)
    subprocess.run(["smbcontrol", "smbd", "close-share", "Media"], check=False, capture_output=True)
    os.sync()
    if source_for_target(target):
        result = run(["umount", str(target)], check=False, timeout=20)
        if result.returncode != 0:
            subprocess.run(["systemctl", "start", "minidlna.service"], check=False)
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"Supporto ancora in uso; chiudi player/download e riprova. {detail}".strip())
    subprocess.run(["blockdev", "--flushbufs", info["device"]], check=False, capture_output=True)
    if removable_disk_safe_to_delete(info):
        delete = Path("/sys/class/block") / info["disk_name"] / "device/delete"
        try:
            delete.write_text("1\n")
        except OSError:
            pass
    try:
        target.rmdir()
    except OSError:
        pass
    subprocess.run(["systemctl", "start", "minidlna.service"], check=False)
    print(f"{info['label']} espulsa in sicurezza: ora puoi rimuoverla.")


def mounted_media_targets(mounts: dict[str, dict] | None = None) -> list[Path]:
    rows = mounts or mount_table()
    prefix = str(MEDIA_ROOT) + "/"
    return [Path(target) for target in rows if target.startswith(prefix)]


def _kernel_reconcile_signature(mounts: dict[str, dict] | None = None) -> str:
    """Cheap hotplug fingerprint: no lsblk and no filesystem data reads."""
    uuid_links = []
    try:
        for link in sorted(UUID_DIR.iterdir(), key=lambda path: path.name):
            if not link.is_symlink():
                continue
            try:
                target = str(link.resolve(strict=True))
            except OSError:
                target = "missing"
            uuid_links.append((link.name, target))
    except OSError:
        pass
    rows = mounts or mount_table()
    media_mounts = [
        {"target": target, "source": row.get("source"), "major_minor": row.get("major_minor"),
         "options": sorted(row.get("options") or [])}
        for target, row in sorted(rows.items())
        if target.startswith(str(MEDIA_ROOT) + "/")
    ]
    payload = json.dumps({"uuid_links": uuid_links, "mounts": media_mounts}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_reconcile_signature() -> str:
    try:
        return str(json.loads(RECONCILE_STATE.read_text(encoding="utf-8")).get("signature") or "")
    except (OSError, ValueError, json.JSONDecodeError):
        return ""


def _write_reconcile_signature(signature: str) -> None:
    try:
        RECONCILE_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RECONCILE_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"signature": signature, "at": int(time.time())}, separators=(",", ":")), encoding="utf-8")
        tmp.replace(RECONCILE_STATE)
    except OSError:
        pass


def reconcile() -> None:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    mounts = mount_table()
    initial_signature = _kernel_reconcile_signature(mounts)
    if initial_signature == _read_reconcile_signature():
        return

    present = {item["uuid"]: item for item in discover()}
    expected_targets = {Path(item["mountpoint"]) for item in present.values()}
    changed = False
    for target in mounted_media_targets(mounts):
        if target not in expected_targets:
            subprocess.run(["umount", str(target)], check=False, capture_output=True)
            mounts = mount_table()
            if source_for_target(target, mounts):
                subprocess.run(["umount", "-l", str(target)], check=False, capture_output=True)
            changed = True
    for uuid, info in present.items():
        try:
            mounts = mount_table()
            before = source_for_target(Path(info["mountpoint"]), mounts)
            mount_media(uuid, quiet=True, info=info)
            changed = changed or not before
        except RuntimeError as exc:
            print(f"media {uuid}: {exc}", file=sys.stderr)
    mounts = mount_table()
    for child in MEDIA_ROOT.iterdir():
        if child.is_dir() and not source_for_target(child, mounts):
            try:
                child.rmdir()
            except OSError:
                pass
    if changed:
        restart_indexer()
    _write_reconcile_signature(_kernel_reconcile_signature(mount_table()))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    sub.add_parser("reconcile")
    for name in ("mount", "eject"):
        p = sub.add_parser(name)
        p.add_argument("uuid")
    args = parser.parse_args()
    if os.geteuid() != 0 and args.action != "list":
        raise RuntimeError("Root required")
    if args.action == "list":
        print(json.dumps(discover(), ensure_ascii=False))
    elif args.action == "mount":
        mount_media(args.uuid)
    elif args.action == "eject":
        eject_media(args.uuid)
    else:
        reconcile()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            message = (exc.stderr or exc.stdout or str(exc)).strip()
        else:
            message = str(exc)
        print(message, file=sys.stderr)
        raise SystemExit(1)
