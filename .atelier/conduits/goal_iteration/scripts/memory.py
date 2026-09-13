"""Shared, git-ignored idea history for linked worktrees (stdlib only)."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def git(cwd, *args):
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def shared_root(cwd):
    common = Path(git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    main = common.parent
    if common.name != ".git" or Path(git(main, "rev-parse", "--show-toplevel")).resolve() != main.resolve():
        raise RuntimeError("Shared memory requires a normal main checkout with a .git directory")
    if git(main, "ls-files", ".atelier"):
        raise RuntimeError("Main checkout tracks .atelier files; refusing shared memory")
    root = main / ".atelier" / "implementations"
    root.mkdir(parents=True, exist_ok=True)
    # info/exclude belongs to the shared Git directory, so this covers every worktree.
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    old = exclude.read_text() if exclude.exists() else ""
    if "/.atelier/" not in old.splitlines():
        with exclude.open("a") as out:
            out.write("\n/.atelier/\n")
    return root


def attach(cwd):
    root = shared_root(cwd)
    local = cwd / ".atelier" / "implementations"
    local.parent.mkdir(parents=True, exist_ok=True)
    if local.resolve() == root.resolve():
        return root
    if local.is_symlink():
        raise RuntimeError(f"Unexpected memory symlink: {local}; inspect it manually")
    if local.exists():
        # Keep originals as a backup and copy the complete directory, not just successful ideas.
        key = cwd.name + "-" + hashlib.sha256(str(cwd).encode()).hexdigest()[:8]
        destination = root / "imported" / key
        for src in local.rglob("*"):
            if src.is_symlink():
                raise RuntimeError(f"Unexpected symlink in legacy history: {src}")
            if not src.is_file():
                continue
            target = destination / src.relative_to(local)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != src.read_bytes():
                raise RuntimeError(f"Conflicting history: {target}")
            shutil.copy2(src, target)
        backup = local.with_name("implementations-local-backup")
        if backup.exists():
            raise RuntimeError(f"Backup already exists: {backup}; inspect before migration")
        local.rename(backup)
    local.symlink_to(root, target_is_directory=True)
    return root


def index(root):
    rows = ["# Shared decision history", "",
            "Read this index before proposing an idea, then open relevant linked documents.",
            "KEEP is an accepted experiment, not proof its commit exists on your current branch.",
            "INCOMPLETE / UNREVIEWED includes interrupted proposals, failed implementations and pending reviews.",
            "Do not repeat an idea without understanding its prior outcome and explaining new evidence.", ""]
    for doc in sorted(root.rglob("*.md")):
        if doc.name in {"index.md", "INDEX.md"}:
            continue
        body = doc.read_text(errors="replace")
        verdicts = re.findall(r"^VERDICT:\s*(KEEP|DISCARD)\s*$", body, re.M)
        verdict = verdicts[-1] if verdicts else "INCOMPLETE / UNREVIEWED"
        # Include the idea and the review reason, keeping the full record one link away.
        idea = re.split(r"^## ", body, maxsplit=1, flags=re.M)[0]
        reason = body.rsplit("## Verdict", 1)[-1] if "## Verdict" in body else "No final verdict recorded."
        def compact(value):
            return " ".join(value.split()).replace("|", "\\|")[:600]
        rows.extend([f"- [{doc.relative_to(root)}]({doc.relative_to(root).as_posix()}) — **{verdict}**",
                     f"  {compact(idea)}", f"  {compact(reason)}"])
    # An interrupted writer must never leave a truncated index.
    fd, name = tempfile.mkstemp(dir=root, prefix=".index-")
    with os.fdopen(fd, "w") as out:
        out.write("\n".join(rows) + "\n")
    os.replace(name, root / "index.md")


def reserve(root):
    ids = [int(m.group(1)) for p in root.glob("*.md")
           if (m := re.match(r"^(\d+)(?:-|\.md$)", p.name))]
    n = max(ids, default=0) + 1
    while True:
        identifier = f"{n:04d}"
        try:
            with (root / f"{identifier}.md").open("x") as out:
                out.write("# Idea\nPending Codex proposal.\n\n## Status\nINCOMPLETE / UNREVIEWED\n")
            return identifier
        except FileExistsError:
            n += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["attach", "next", "index"])
    args = parser.parse_args()
    cwd = Path.cwd().resolve()
    root = attach(cwd)
    identifier = reserve(root) if args.action == "next" else None
    index(root)
    if identifier:
        print(identifier, end="")
    elif args.action == "attach":
        print(root)


if __name__ == "__main__":
    main()
