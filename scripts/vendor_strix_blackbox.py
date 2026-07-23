#!/usr/bin/env python3
"""Vendor the Apache-2.0 Strix black-box skill corpus deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


EXCLUDED = {
    "README.md",
    "coordination/root_agent.md",
    "coordination/source_aware_whitebox.md",
    "custom/dependency_cve_scanning.md",
    "custom/source_aware_sast.md",
    "tooling/semgrep.md",
}


def _blackbox_scan_mode(content: str) -> str:
    """Remove explicitly labelled white-box blocks from scan-mode guidance."""
    return re.sub(
        r"\*\*Whitebox \(source available\)\*\*.*?"
        r"\*\*Blackbox \(no source\)\*\*\s*",
        "",
        content,
        flags=re.DOTALL,
    )


def _skill_name(relative: Path) -> str:
    return "strix-" + "-".join((*relative.parts[:-1], relative.stem)).replace("_", "-")


def _rewrite_skill(content: str, relative: Path, commit: str) -> str:
    content = _blackbox_scan_mode(content)
    if relative.as_posix() == "frameworks/django.md":
        content = re.sub(
            r"## Tooling\n.*?(?=\n## Summary)",
            "## Tooling\n\n"
            "Use black-box HTTP tooling, browser flows, Caido history, and "
            "role-separated test accounts. Do not inspect application source.\n",
            content,
            flags=re.DOTALL,
        )
    name = _skill_name(relative)
    content = re.sub(r"(?m)^name:\s*.+$", f"name: {name}", content, count=1)
    marker = f"\n> Vendored from `usestrix/strix@{commit}` (`strix/skills/{relative.as_posix()}`), Apache-2.0.\n"
    boundary = content.find("---", 3)
    if boundary < 0:
        raise ValueError(f"Missing YAML frontmatter: {relative}")
    boundary += 3
    return content[:boundary] + marker + content[boundary:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Checked-out usestrix/strix repository")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(".flocks/skills"),
    )
    args = parser.parse_args()

    source = args.source.resolve()
    skills_root = source / "strix" / "skills"
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    manifest = {
        "upstream": "https://github.com/usestrix/strix",
        "commit": commit,
        "license": "Apache-2.0",
        "files": [],
        "assets": [],
    }
    for source_file in sorted(skills_root.rglob("*.md")):
        relative = source_file.relative_to(skills_root)
        if relative.as_posix() in EXCLUDED:
            continue
        content = _rewrite_skill(
            source_file.read_text(encoding="utf-8"),
            relative,
            commit,
        )
        target = destination / _skill_name(relative) / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        manifest["files"].append(
            {
                "source": f"strix/skills/{relative.as_posix()}",
                "destination": str(target.relative_to(destination.parent.parent)),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )

    vendor_root = Path("flocks/pentest/vendor/strix")
    vendor_root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "LICENSE", vendor_root / "LICENSE")
    upstream_prompt = source / "strix" / "skills" / "coordination" / "root_agent.md"
    shutil.copyfile(upstream_prompt, vendor_root / "root_agent.upstream.md")
    (vendor_root / "NOTICE").write_text(
        "Strix black-box prompts and skills\n"
        "Copyright usestrix contributors\n"
        "Licensed under the Apache License, Version 2.0.\n",
        encoding="utf-8",
    )
    (vendor_root / "manifest.json").write_text(
        json.dumps(
            {
                **manifest,
                "assets": [
                    {
                        "source": "strix/skills/coordination/root_agent.md",
                        "destination": "flocks/pentest/vendor/strix/root_agent.upstream.md",
                        "sha256": hashlib.sha256(upstream_prompt.read_bytes()).hexdigest(),
                    },
                    {
                        "source": "containers/Dockerfile",
                        "destination": "containers/pentest/Dockerfile",
                        "upstream_sha256": hashlib.sha256(
                            (source / "containers" / "Dockerfile").read_bytes()
                        ).hexdigest(),
                        "adapted_sha256": hashlib.sha256(
                            Path("containers/pentest/Dockerfile").read_bytes()
                        ).hexdigest(),
                    },
                    {
                        "source": "strix/tools/proxy/caido_api.py",
                        "destination": ("containers/pentest/strix/tools/proxy/caido_api.py"),
                        "sha256": hashlib.sha256(
                            (source / "strix" / "tools" / "proxy" / "caido_api.py").read_bytes()
                        ).hexdigest(),
                    },
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
