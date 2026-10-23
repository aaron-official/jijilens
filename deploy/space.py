"""Push the repo to its Hugging Face Space and configure it.

    uv run python deploy/space.py                  # push code, set SNAPSHOT_REPO
    JIJILENS_SNAPSHOT_TOKEN=hf_... uv run python deploy/space.py   # also set the read token secret

Only git-tracked files are pushed, so data/ and .env never leave the machine.
The Space's README gets the front matter Spaces need; the repo's own README
stays as it is.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from huggingface_hub import CommitOperationAdd, HfApi

ROOT = Path(__file__).resolve().parents[1]
SPACE = os.environ.get("JIJILENS_SPACE", "aaron-official/jijilens")
SNAPSHOT_REPO = os.environ.get("JIJILENS_SNAPSHOT_REPO", "aaron-official/jijilens-snapshot")

FRONT_MATTER = """---
title: JijiLens
emoji: 🔎
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
short_description: Tap anything in a photo, find it on Jiji Uganda
---

"""


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def main() -> None:
    api = HfApi()
    api.create_repo(SPACE, repo_type="space", space_sdk="docker", private=False, exist_ok=True)

    # Settings first, so the build that the push starts already has them.
    api.add_space_variable(SPACE, "SNAPSHOT_REPO", SNAPSHOT_REPO)
    token = os.environ.get("JIJILENS_SNAPSHOT_TOKEN")
    if token:
        api.add_space_secret(SPACE, "HF_TOKEN", token, description="read-only access to the snapshot dataset")

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    operations = []
    for name in tracked_files():
        if name == "README.md":
            content = FRONT_MATTER + (ROOT / name).read_text(encoding="utf-8")
            operations.append(CommitOperationAdd(name, content.encode("utf-8")))
        else:
            operations.append(CommitOperationAdd(name, str(ROOT / name)))
    api.create_commit(SPACE, repo_type="space", operations=operations, commit_message=f"Deploy {commit}")
    print(f"pushed {commit} to https://huggingface.co/spaces/{SPACE}")
    if not token:
        print("HF_TOKEN secret not changed; set JIJILENS_SNAPSHOT_TOKEN to set it")


if __name__ == "__main__":
    main()
