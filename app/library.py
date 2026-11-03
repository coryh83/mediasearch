import shutil
from pathlib import Path


def move_to_library(complete_dir: str, name: str, dest_dir: str) -> Path:
    """Move a finished download from the complete folder into a library folder.

    Never overwrites anything: if the destination already has that name, it stops.
    """
    # The name comes from Transmission. Refuse anything that could point outside the folders.
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"unsafe torrent name: {name!r}")

    src = Path(complete_dir) / name
    if not src.exists():
        raise FileNotFoundError(f"not found in the complete folder: {src}")

    dest_root = Path(dest_dir)
    if not dest_root.is_dir():
        raise RuntimeError(f"destination folder is missing: {dest_root}")

    dst = dest_root / name
    if dst.exists():
        raise RuntimeError(f"already exists in the destination: {dst}")

    shutil.move(str(src), str(dst))
    return dst
