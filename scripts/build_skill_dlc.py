#!/usr/bin/env python3
"""Build an allowlisted, deterministic standalone skill ZIP and .skill archive."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def build(output, root=ROOT):
    manifest = json.loads((root / "dlc/pid-tuning.json").read_text(encoding="utf-8"))
    name, version = manifest["name"], manifest["version"]
    if name != "pid-tuning" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid skill name or release version")
    source = root / "skills" / name
    frontmatter = (source / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    if not re.search(r'^  version: "' + re.escape(version) + r'"$', frontmatter, re.MULTILINE):
        raise ValueError("Skill metadata version does not match DLC manifest")
    files = manifest["files"]
    if len(set(files)) != len(files) or not {"SKILL.md", "LICENSE"}.issubset(files):
        raise ValueError("Invalid manifest file list")
    payloads = {}
    for relative in sorted(files):
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
            raise ValueError(f"Unsafe manifest path: {relative}")
        full = source.joinpath(*path.parts)
        if not full.resolve().is_relative_to(source.resolve()) or not full.is_file():
            raise ValueError(f"Missing or external skill file: {relative}")
        payloads[f"{name}/{relative}"] = full.read_bytes()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-skill-v{version}"
    archive = output / f"{stem}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name_in_zip, data in payloads.items():
            info = zipfile.ZipInfo(name_in_zip, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data, compresslevel=9)
    skill_archive = archive.with_suffix(".skill")
    skill_archive.write_bytes(archive.read_bytes())
    checksums = output / f"{stem}-SHA256SUMS.txt"
    checksums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                                 for p in (archive, skill_archive)), encoding="utf-8")
    return [archive, skill_archive, checksums]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/skill-dlc")
    arguments = parser.parse_args()
    for artifact in build(arguments.output):
        print(artifact)
