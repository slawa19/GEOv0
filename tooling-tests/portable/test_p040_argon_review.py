"""040 slice A: `scripts/argon_review.py` refuses, verifies and keeps the key out, with no network.

WHAT THIS PROVES. On a temporary git repository with real commits, driven as a subprocess (`sys.executable -I`):

* before anything is sent the script refuses (exit 3, no run directory): live mode without a key, a SHA that is
  not a commit, a touched path that looks like a secret, a payload that contains the key, a payload over
  `MAX_PAYLOAD_CHARS` - and that one is refused, not truncated;
* `--dry-run` writes the prompt, the payload (the commit list, the diff and every touched text file in full) and
  the meta, makes no call, and needs no key; a binary file is named in the meta and not embedded;
* the payload comes from committed objects: a dirty working tree and an untracked file never reach it;
* `--verify-response` on a saved response: exit 0 only with HTTP-shaped success, `finish_reason == "stop"`,
  content and all three markers; each missing marker, `length`, empty content and an error body are exit 2;
  `cost` is the number of `usage.cost` and the word `unknown` without it;
* the key (`OPENROUTER_API_KEY=sk-or-TESTMARKER-123`) is in no file of the run directory and in no output.

The live path (`post`) is exercised in-process with `post` replaced: a successful answer and a transport failure.
No test opens a socket.

WHAT THIS DOES NOT PROVE. That OpenRouter really answers in the shape the saved responses have, that the model id
is served, or what a call costs: that is the first real call (040 `T4030`), recorded in the ledger.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "argon_review.py"
KEY = "sk-or-TESTMARKER-123"
MARKED = "Findings: none.\nVERDICT-040-A: SOUND\nCLASS-1-COUNT: 0\nREVIEW-COMPLETE: YES\n"


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _commit(repo: Path, files: dict[str, str | bytes], message: str) -> str:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> dict:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    base = _commit(root, {"app/a.py": "print('base')\n"}, "base")
    head = _commit(root, {"app/a.py": "print('COMMITTED-HEAD')\n", "app/b.py": "B = 1\n"}, "head change")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Review this. PROMPT-SENTINEL\n", encoding="utf-8")
    return {"root": root, "base": base, "head": head, "prompt": prompt, "tmp": tmp_path}


def _run(repo: dict, *extra: str, key: str | None = None, out: Path | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    if key is not None:
        env["OPENROUTER_API_KEY"] = key
    out = out or repo["tmp"] / "out"
    args = ["--base", repo["base"], "--head", repo["head"], "--prompt", str(repo["prompt"]), "--out", str(out)]
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args, *extra], cwd=repo["root"], env=env,
                          capture_output=True, text=True, timeout=120)


def _response(tmp: Path, content: str = MARKED, finish: str = "stop", cost: object = 0.0123, **over) -> Path:
    usage: dict = {"prompt_tokens": 1000, "completion_tokens": 200}
    if cost is not None:
        usage["cost"] = cost
    body = {"id": "gen-1", "model": "google/gemini-4-argon-20261013", "usage": usage,
            "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}], **over}
    path = tmp / "saved_response.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _meta(out: Path) -> dict:
    return json.loads((out / "meta.json").read_text(encoding="utf-8"))


# --- refusals before anything is sent ----------------------------------------------------------------------------

def test_live_mode_without_a_key_is_refused_before_a_run_directory_exists(repo: dict) -> None:
    result = _run(repo)
    assert result.returncode == 3, result.stderr
    assert "OPENROUTER_API_KEY" in result.stderr
    assert not (repo["tmp"] / "out").exists()


def test_a_sha_that_is_not_a_commit_is_refused(repo: dict) -> None:
    repo = {**repo, "head": "0123456789abcdef0123456789abcdef01234567"}
    result = _run(repo, "--dry-run")
    assert result.returncode == 3, result.stderr
    assert "does not resolve to a commit" in result.stderr
    assert not (repo["tmp"] / "out").exists()


def test_a_payload_over_the_limit_is_refused_and_never_truncated(repo: dict) -> None:
    big = _commit(repo["root"], {"app/big.txt": "x" * 1_600_000 + "\nTAIL-SENTINEL\n"}, "big")
    result = _run({**repo, "head": big}, "--dry-run")
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "MAX_PAYLOAD_CHARS" in result.stderr and "nothing is truncated" in result.stderr
    assert not (repo["tmp"] / "out" / "payload.json").exists()


@pytest.mark.parametrize("secret", [".env.local", "config/Server.PEM", "deploy/id_rsa.key", "Secrets/tokens.txt"])
def test_a_touched_secret_looking_path_is_refused(repo: dict, secret: str) -> None:
    head = _commit(repo["root"], {secret: "value\n"}, "secret")
    result = _run({**repo, "head": head}, "--dry-run")
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "look like secrets" in result.stderr and secret in result.stderr


def test_a_payload_that_contains_the_key_is_refused(repo: dict) -> None:
    head = _commit(repo["root"], {"app/leak.py": f"KEY = '{KEY}'\n"}, "leak")
    result = _run({**repo, "head": head}, "--dry-run", key=KEY)
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert KEY not in result.stdout + result.stderr
    assert not (repo["tmp"] / "out").exists()


# --- what is sent ------------------------------------------------------------------------------------------------

def test_dry_run_writes_the_payload_and_makes_no_call_without_a_key(repo: dict) -> None:
    out = repo["tmp"] / "out"
    result = _run(repo, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == ["meta.json", "payload.json", "progress.log", "prompt.txt"]
    body = json.loads((out / "payload.json").read_text(encoding="utf-8"))
    assert body["model"] == "google/gemini-4-argon" and body["temperature"] == 0 and body["usage"] == {"include": True}
    text = body["messages"][0]["content"]
    assert text.startswith("Review this. PROMPT-SENTINEL")
    assert "+print('COMMITTED-HEAD')" in text and "-print('base')" in text  # the diff
    assert "### app/b.py" in text and "head change" in text  # a full file, the commit list
    meta = _meta(out)
    assert meta["status"] == "DRY-RUN" and meta["payload_chars"] == len(text)
    assert meta["base"] == repo["base"] and meta["head"] == repo["head"]
    assert (out / "prompt.txt").read_text(encoding="utf-8") == "Review this. PROMPT-SENTINEL\n"


def test_the_payload_comes_from_commits_not_from_the_working_tree(repo: dict) -> None:
    (repo["root"] / "app" / "a.py").write_text("print('DIRTY-WORKTREE')\n", encoding="utf-8")
    (repo["root"] / "app" / "untracked.py").write_text("UNTRACKED-SENTINEL\n", encoding="utf-8")
    result = _run(repo, "--dry-run")
    assert result.returncode == 0, result.stderr
    payload = (repo["tmp"] / "out" / "payload.json").read_text(encoding="utf-8")
    assert "DIRTY-WORKTREE" not in payload and "UNTRACKED-SENTINEL" not in payload
    assert "print('COMMITTED-HEAD')" in payload


def test_a_binary_file_is_named_in_the_meta_and_not_embedded(repo: dict) -> None:
    head = _commit(repo["root"], {"img/logo.png": b"\x89PNG\x00\x01BINARY-SENTINEL\x00"}, "binary")
    out = repo["tmp"] / "out"
    result = _run({**repo, "head": head}, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert _meta(out)["skipped_binaries"] == ["img/logo.png"]
    assert "BINARY-SENTINEL" not in (out / "payload.json").read_text(encoding="utf-8")


# --- verification of a saved response -----------------------------------------------------------------------------

def test_a_saved_response_with_all_markers_verifies_and_records_the_reported_cost(repo: dict) -> None:
    out = repo["tmp"] / "out"
    result = _run(repo, "--verify-response", str(_response(repo["tmp"])))
    assert result.returncode == 0, result.stderr
    meta = _meta(out)
    assert meta["status"] == "VERIFIED" and meta["cost"] == 0.0123
    assert meta["response_model"] == "google/gemini-4-argon-20261013" and meta["generation_id"] == "gen-1"
    assert (meta["prompt_tokens"], meta["completion_tokens"], meta["finish_reason"]) == (1000, 200, "stop")
    assert (out / "final.md").read_text(encoding="utf-8") == MARKED
    assert "| 0.0123 |" in result.stdout  # the ledger stub carries the number


def test_cost_is_unknown_when_the_response_reports_none(repo: dict) -> None:
    result = _run(repo, "--verify-response", str(_response(repo["tmp"], cost=None)))
    assert result.returncode == 0, result.stderr
    assert _meta(repo["tmp"] / "out")["cost"] == "unknown"


@pytest.mark.parametrize("marker", ["VERDICT-040-A: SOUND", "CLASS-1-COUNT: 0", "REVIEW-COMPLETE: YES"])
def test_a_missing_marker_is_unverified(repo: dict, marker: str) -> None:
    result = _run(repo, "--verify-response", str(_response(repo["tmp"], content=MARKED.replace(marker, ""))))
    assert result.returncode == 2, result.stderr
    meta = _meta(repo["tmp"] / "out")
    assert meta["status"] == "UNVERIFIED" and "markers missing" in meta["reason"]


@pytest.mark.parametrize(
    ("kind", "over"),
    [("length", {"finish": "length"}), ("empty content", {"content": "  \n"}),
     ("error body", {"choices": [], "error": {"code": 502, "message": "upstream"}})],
)
def test_an_incomplete_response_is_unverified(repo: dict, kind: str, over: dict) -> None:
    result = _run(repo, "--verify-response", str(_response(repo["tmp"], **over)))
    assert result.returncode == 2, (kind, result.stderr)
    assert _meta(repo["tmp"] / "out")["status"] == "UNVERIFIED"


# --- the key ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["--dry-run", "--verify-response"])
def test_the_key_is_in_no_written_file_and_no_output(repo: dict, mode: str) -> None:
    out = repo["tmp"] / "out"
    extra = [mode] if mode == "--dry-run" else [mode, str(_response(repo["tmp"]))]
    result = _run(repo, *extra, key=KEY)
    assert result.returncode == 0, result.stderr
    written = [p for p in out.iterdir()]
    assert len(written) >= 4
    for path in written:
        assert KEY not in path.read_text(encoding="utf-8"), path.name
    assert KEY not in result.stdout + result.stderr


# --- the live path, in process, with `post` replaced --------------------------------------------------------------

def _load_module():
    spec = importlib.util.spec_from_file_location("argon_review_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_live_path_sends_one_request_and_keeps_the_key_out_of_every_file(
    repo: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, calls = _load_module(), []

    def fake_post(body: dict, key: str, timeout: float) -> tuple[int, str]:
        calls.append((body, key))
        return 200, _response(repo["tmp"]).read_text(encoding="utf-8")

    monkeypatch.setattr(module, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.chdir(repo["root"])
    out = repo["tmp"] / "out"
    code = module.main(["--base", repo["base"], "--head", repo["head"], "--prompt", str(repo["prompt"]),
                        "--out", str(out)])
    assert code == 0
    assert len(calls) == 1 and calls[0][1] == KEY and calls[0][0]["temperature"] == 0
    assert _meta(out)["mode"] == "live"
    for path in out.iterdir():
        assert KEY not in path.read_text(encoding="utf-8"), path.name


def test_a_transport_failure_on_the_live_path_is_unverified(repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()

    def failing_post(body: dict, key: str, timeout: float) -> tuple[int, str]:
        raise urllib.error.URLError(f"refused while sending {key}")

    monkeypatch.setattr(module, "post", failing_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.chdir(repo["root"])
    out = repo["tmp"] / "out"
    code = module.main(["--base", repo["base"], "--head", repo["head"], "--prompt", str(repo["prompt"]),
                        "--out", str(out)])
    assert code == 2
    meta = _meta(out)
    assert meta["status"] == "UNVERIFIED" and "transport error" in meta["reason"]
    assert KEY not in (out / "response.json").read_text(encoding="utf-8")
