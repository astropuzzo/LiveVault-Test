#!/usr/bin/env python3
"""OpenAstro removable-media manager.

Only USB filesystems marked removable by the kernel are eligible. LiveVault's
SERVER and SHARE UUIDs are permanently excluded. Media is mounted read-only
under /srv/openastro-media so TV/PC/browser clients cannot damage source files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MEDIA_ROOT = Path("/srv/openastro-media")
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


def source_for_target(target: Path) -> str | None:
    result = run(["findmnt", "-nro", "SOURCE", "--mountpoint", str(target)], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def mount_options(info: dict) -> str:
    common = ["ro", "nosuid", "nodev", "noexec"]
    fs = info["fstype"]
    if fs in {"exfat", "vfat", "fat", "fat32"}:
        common += ["uid=1001", "gid=1001", "fmask=0133", "dmask=0022"]
    elif fs in {"ntfs", "ntfs3"}:
        common += ["uid=1001", "gid=1001", "umask=022"]
    return ",".join(common)


def restart_indexer() -> None:
    subprocess.run(["systemctl", "try-restart", "minidlna.service"], check=False, capture_output=True)


def mount_media(uuid: str, *, quiet: bool = False) -> dict:
    info = get_info(uuid)
    target = Path(info["mountpoint"])
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    target.mkdir(parents=True, exist_ok=True, mode=0o755)
    current = source_for_target(target)
    real_device = str(Path(f"/dev/disk/by-uuid/{uuid}").resolve())
    if current:
        if Path(current).resolve() != Path(real_device).resolve():
            raise RuntimeError(f"Mountpoint già usato da {current}")
        return info
    if info["existing_mounts"]:
        raise RuntimeError(f"Supporto già montato altrove: {', '.join(info['existing_mounts'])}")
    run(["mount", "-o", mount_options(info), f"/dev/disk/by-uuid/{uuid}", str(target)])
    current = source_for_target(target)
    if not current or Path(current).resolve() != Path(real_device).resolve():
        subprocess.run(["umount", str(target)], check=False)
        raise RuntimeError("Verifica mount media fallita")
    restart_indexer()
    if not quiet:
        print(f"{info['label']} montata read-only in {target}")
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


def mounted_media_targets() -> list[Path]:
    targets: list[Path] = []
    result = run(["findmnt", "-rn", "-o", "TARGET"], check=False)
    for line in result.stdout.splitlines():
        path = Path(line.strip())
        if str(path).startswith(str(MEDIA_ROOT) + "/") and path not in targets:
            targets.append(path)
    return targets


def reconcile() -> None:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    present = {item["uuid"]: item for item in discover()}
    expected_targets = {Path(item["mountpoint"]) for item in present.values()}
    changed = False
    for target in mounted_media_targets():
        if target not in expected_targets:
            subprocess.run(["umount", str(target)], check=False, capture_output=True)
            if source_for_target(target):
                subprocess.run(["umount", "-l", str(target)], check=False, capture_output=True)
            changed = True
    for uuid in present:
        try:
            before = source_for_target(Path(present[uuid]["mountpoint"]))
            mount_media(uuid, quiet=True)
            changed = changed or not before
        except RuntimeError as exc:
            print(f"media {uuid}: {exc}", file=sys.stderr)
    for child in MEDIA_ROOT.iterdir():
        if child.is_dir() and not source_for_target(child):
            try:
                child.rmdir()
            except OSError:
                pass
    if changed:
        restart_indexer()


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
