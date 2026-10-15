"""Self-editing: a resident changes its own harness, with git as the undo.

`edit_harness` edits, creates or deletes files inside the `resident/`
package of the checkout the resident runs from. Every change is checked
before it is kept — it must parse, and the whole package must still import
in a fresh interpreter — and is committed on its own. The supervisor then
restarts the resident onto the new code, and if the new code crashes, rolls
the change back.

A checkout may be shared: other residents, people, and other tools commit
in the same tree. So nothing here touches more than it means to:

- Every commit is path-limited (`git commit -- <path>`): only the file the
  resident changed, never anyone else's staged or unstaged work.
- Commits carry the resident's own prefix ("<Name> self-edit:"), and the
  crash rollback asks "is the newest change to resident/ MY self-edit?" —
  so one resident's crash can never revert another's edit, or a person's.
- Rollback restores only the paths that commit touched, from its parent;
  never `git revert HEAD`, which reverts whatever happens to be on top.
- Every target must resolve inside the package.

Several residents on one checkout share one harness, so one resident's edit
changes the code all of them run. Give a self-editing resident its own
clone (or `git worktree`) if that isn't what you want.
"""

import ast
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("selfedit")

PACKAGE = "resident"
LOAD_BEARING = {"__init__.py", "__main__.py"}


class SelfEdit:
    def __init__(self, repo: Path, name: str, package: str = PACKAGE):
        self.repo = Path(repo).resolve()
        self.pkg = self.repo / package
        self.package = package
        self.prefix = f"{name} self-edit:"
        self.name = name

    # ---- git -----------------------------------------------------------------

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True,
                              text=True, timeout=30)

    def _rel(self, target: Path) -> str:
        return target.resolve().relative_to(self.repo).as_posix()

    def _inside(self, target: Path) -> bool:
        try:
            target.resolve().relative_to(self.pkg.resolve())
            return True
        except ValueError:
            return False

    def _checkpoint(self, target: Path, why: str):
        """Commit uncommitted changes to this one file first, so rolling the
        self-edit back restores exactly what was there before it."""
        rel = self._rel(target)
        if self.git("status", "--porcelain", "--", rel).stdout.strip():
            self.git("commit", "-m", f"{self.name} pre-edit checkpoint (before: {why})",
                     "--", rel)

    # ---- checks --------------------------------------------------------------

    @staticmethod
    def parse_error(path: Path, source: str) -> str | None:
        if path.suffix != ".py":
            return None
        try:
            ast.parse(source, filename=str(path))
            return None
        except SyntaxError as e:
            return f"{e.msg} (line {e.lineno})"

    def import_error(self) -> str | None:
        """Import every module of the package in a fresh interpreter, against
        the files on disk — catches what parsing cannot (a NameError at import,
        a removed symbol something else still uses)."""
        script = ("import importlib, pkgutil, sys\n"
                  f"sys.path.insert(0, {str(self.repo)!r})\n"
                  f"import {self.package}\n"
                  f"for m in pkgutil.walk_packages({self.package}.__path__, "
                  f"'{self.package}.'):\n"
                  "    importlib.import_module(m.name)\n")
        try:
            p = subprocess.run([sys.executable, "-c", script], cwd=self.repo,
                               capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return "import check timed out (120s)"
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()
            return "\n".join(err.splitlines()[-12:]) or f"import failed (exit {p.returncode})"
        return None

    # ---- the three actions ---------------------------------------------------

    def edit(self, file: str, edits: list[dict], why: str) -> dict:
        target = self.pkg / file
        if not self._inside(target):
            return _err(f"{file} is outside {self.package}/.")
        if not target.is_file():
            return _err(f"No file {file} — use action='create' for a new one.")
        original = target.read_text()
        content = original
        for i, e in enumerate(edits):
            old, new = e.get("old_string") or "", e.get("new_string", "")
            if not old or old == new:
                return _err(f"edits[{i}]: old_string must be non-empty and differ "
                            "from new_string.")
            n = content.count(old)
            if n == 0:
                return _err(f"edits[{i}]: old_string not found (nothing changed).")
            if n > 1 and not e.get("replace_all"):
                return _err(f"edits[{i}]: old_string found {n} times — make it "
                            "unique or pass replace_all (nothing changed).")
            content = content.replace(old, new) if e.get("replace_all") \
                else content.replace(old, new, 1)
        bad = self.parse_error(target, content)
        if bad:
            return _err(f"Not applied — it would not parse: {bad}")
        self._checkpoint(target, why)
        target.write_text(content)
        bad = self.import_error()
        if bad:
            target.write_text(original)
            return _err(f"Rolled back — the package no longer imports:\n{bad}")
        return self._commit([self._rel(target)], why, f"Edited {file}")

    def create(self, file: str, content: str, why: str) -> dict:
        target = self.pkg / file
        if not self._inside(target):
            return _err(f"{file} is outside {self.package}/.")
        if target.exists():
            return _err(f"{file} exists — use action='edit'.")
        bad = self.parse_error(target, content)
        if bad:
            return _err(f"Not created — it would not parse: {bad}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        bad = self.import_error()
        if bad:
            target.unlink()
            return _err(f"Rolled back — the package no longer imports:\n{bad}")
        rel = self._rel(target)
        self.git("add", "--", rel)
        return self._commit([rel], why, f"Created {file}")

    def delete(self, file: str, why: str) -> dict:
        target = self.pkg / file
        if not self._inside(target):
            return _err(f"{file} is outside {self.package}/.")
        if not target.is_file():
            return _err(f"No file {file}.")
        if target.resolve().relative_to(self.pkg.resolve()).as_posix() in LOAD_BEARING:
            return _err(f"{file} is load-bearing; edit it instead.")
        self._checkpoint(target, why)
        rel = self._rel(target)
        target.unlink()
        bad = self.import_error()
        if bad:
            self.git("checkout", "HEAD", "--", rel)
            return _err(f"Rolled back — something still imports it:\n{bad}")
        self.git("rm", "--cached", "--quiet", "--", rel)
        return self._commit([rel], why, f"Deleted {file}")

    def _commit(self, paths: list[str], why: str, done: str) -> dict:
        r = self.git("commit", "-m", f"{self.prefix} {why}", "--", *paths)
        if r.returncode != 0:
            return _err(f"{done}, but the commit failed: {r.stderr.strip()}")
        logger.info(f"{done}: {why}")
        return {"content": f"{done} and committed. You'll restart onto the new code "
                           "when this turn ends; if it crashes, it is rolled back."}

    # ---- for the supervisor --------------------------------------------------

    def _newest_package_commit(self) -> tuple[str, str] | None:
        r = self.git("log", "-1", "--format=%H%x00%s", "--", f"{self.package}/")
        out = r.stdout.strip()
        if r.returncode != 0 or "\x00" not in out:
            return None
        sha, subject = out.split("\x00", 1)
        return sha, subject

    def newest_change_is_mine(self) -> bool:
        last = self._newest_package_commit()
        return bool(last) and last[1].startswith(self.prefix)

    def rollback(self) -> bool:
        """Undo this resident's newest self-edit, restoring only its paths."""
        last = self._newest_package_commit()
        if not last or not last[1].startswith(self.prefix):
            return False
        sha, subject = last
        listing = self.git("show", "--name-status", "--format=", sha).stdout
        paths = []
        for line in listing.splitlines():
            if "\t" not in line:
                continue
            status, path = line.split("\t", 1)
            if not path.startswith(f"{self.package}/"):
                logger.error(f"Rollback refused: {sha[:8]} touches {path}")
                return False
            if status.startswith("A"):
                self.git("rm", "--quiet", "--", path)
            else:
                self.git("checkout", f"{sha}^", "--", path)
            paths.append(path)
        if not paths:
            return False
        r = self.git("commit", "-m", f"{self.name} self-edit reverted after a crash: "
                     f"{subject[len(self.prefix):].strip()}", "--", *paths)
        if r.returncode != 0:
            logger.error(f"Rollback commit failed: {r.stderr.strip()}")
            return False
        logger.warning(f"Rolled back self-edit {sha[:8]}")
        return True


def _err(text: str) -> dict:
    return {"content": text, "is_error": True}


def for_this_checkout(name: str) -> SelfEdit:
    """The checkout this process is running from."""
    return SelfEdit(Path(__file__).resolve().parents[2], name)
