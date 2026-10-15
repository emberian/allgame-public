import subprocess

import pytest

from resident.tools import registry, sandbox


@pytest.fixture
def ctx(state, chat):
    return registry.Context(state=state, chat=chat, stream="lounge", topic="sandbox-test")


def test_the_container_is_limited_and_state_is_read_only(ctx, tmp_path):
    cmd = sandbox.docker_command("echo hi", tmp_path, ctx.state.root)
    joined = " ".join(cmd)
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "--memory" in cmd and "--pids-limit" in cmd and "--network" in cmd
    assert f"{ctx.state.root}:/state:ro" in joined
    assert cmd[-3:] == ["/bin/bash", "-c", "echo hi"]


def test_files_cannot_escape_the_workspace(ctx):
    out = registry.run("run_sandbox", {"command": "true",
                                       "files": {"../../escape.txt": "x"}}, ctx)
    assert out["is_error"] and "outside" in out["content"]


def test_no_docker_is_a_clear_error(ctx, monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(subprocess, "run", missing)
    out = registry.run("run_sandbox", {"command": "true"}, ctx)
    assert out["is_error"] and "Docker" in out["content"]


def test_output_and_workspace_listing(ctx, monkeypatch):
    def fake(cmd, **k):
        return subprocess.CompletedProcess(cmd, 0, stdout="42\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake)
    out = registry.run("run_sandbox", {"command": "python3 a.py",
                                       "files": {"a.py": "print(42)"}}, ctx)
    assert "42" in out["content"] and "a.py" in out["content"] and "[exit 0]" in out["content"]
