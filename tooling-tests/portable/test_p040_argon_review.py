"""040 slice A: `scripts/argon_review.py` refuses, verifies and keeps the key out, with no network.

WHAT THIS PROVES. On a temporary git repository with real commits, driven as a subprocess (`sys.executable -I`)
through `_GUARD`, which loads the script with every way out of the process closed - `socket.getaddrinfo`,
`socket.create_connection` and `socket.socket.connect` raise `NETWORK-BLOCKED`, and `API_URL` points at the
loopback discard port - so a test that reached for the network fails with that word instead of calling anyone:

* before anything is sent the script refuses (exit 3, no run directory): live mode without a key, a SHA that is
  not a commit, an empty range (also one emptied by `--paths`), a touched path that looks like a secret, a payload
  that contains the key, a payload over `MAX_PAYLOAD_CHARS` - and that one is refused, not truncated - and an
  `--out` that already holds a run;
* the versioned templates `.env*.example` pass; a real `.env` / `.env.local` and an `.example` under a
  secret-looking directory are still refused;
* `--dry-run` writes the prompt, the payload (the commit list, the diff and every touched text file in full) and
  the meta, makes no call, and needs no key; a binary file is named in the meta and not embedded;
* the payload comes from committed objects: a dirty working tree and an untracked file never reach it;
* `--verify-response` on a saved response: exit 0 only with HTTP-shaped success, the requested model (or its dated
  form), `finish_reason == "stop"`, content and all three markers; each missing marker, `length`, empty content,
  an error body, non-JSON and another or no model are exit 2; `cost` is the number of `usage.cost` and the word
  `unknown` without it;
* the key (`OPENROUTER_API_KEY=sk-or-TESTMARKER-123`) is in no file of the run directory, not even redacted, and in
  no output.

The live path is exercised twice: through the guard with the real `post` (urllib reaches the guard and the run is
UNVERIFIED with `NETWORK-BLOCKED` - the counter-check that the guard is not vacuous) and in process with `post`
replaced (a successful answer, a transport failure carrying the key, an HTTP 429 that is not retried). The
socket walls are in-process: they hold for the script's interpreter, not for the git children it starts. The
fixture's own git runs without system or global config and with an empty hooks directory.

The `test_f*` cases are the counterexamples of the external review of `3dad3761` (Codex, 2026-10-16): a signature
helper from repository config seeing the key (F1), a replacement ref swapping a frozen blob (F2), a gitlink
expanded into another commit (F3), malformed markers (F4), model lookalikes (F5), a key with a CR/LF (F6), and an
unbounded response read.

WHAT THIS DOES NOT PROVE. That OpenRouter really answers in the shape the saved responses have, that the model id
is served, or what a call costs: that is the first real call (040 `T4030`), recorded in the ledger.
"""

from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "argon_review.py"
KEY = "sk-or-TESTMARKER-123"
PROMPT = "Review this. PROMPT-SENTINEL\nEnd with:\nVERDICT-<TAG>: SOUND | WEAK | BROKEN\n"
MARKED = "Findings: none.\nVERDICT-040-A: SOUND\nCLASS-1-COUNT: 0\nREVIEW-COMPLETE: YES\n"

#: Runs the script's `main` with the network closed IN THAT PYTHON PROCESS. Each of the three socket walls is
#: enough on its own for urllib; all raise an `OSError` subclass, so the script meets them as it would meet a
#: refused connection. `API_URL` is moved to the loopback discard port as a fourth, independent wall. This is an
#: in-process guard, not a process-tree guarantee: the git children the script starts are outside it (they only
#: read local objects, under the isolated environment `test_f1_every_git_child_...` pins).
_GUARD = """
import importlib.util, socket, sys
class Blocked(ConnectionRefusedError):
    pass
def _blocked(*args, **kwargs):
    raise Blocked("NETWORK-BLOCKED by the 040 test guard")
socket.getaddrinfo = _blocked
socket.create_connection = _blocked
socket.socket.connect = _blocked
spec = importlib.util.spec_from_file_location("argon_review", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.API_URL = "http://127.0.0.1:9/blocked"
sys.exit(module.main(sys.argv[2:]))
"""


def _git_env() -> dict[str, str]:
    """The fixture's own git runs without system or global config (no hooks, signing or helpers from there)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "OPENROUTER_API_KEY"}
    return {**env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def _git(repo: Path, *args: str) -> str:
    no_hooks = repo.parent / "no-hooks"
    no_hooks.mkdir(exist_ok=True)
    return subprocess.run(["git", "-C", str(repo), "-c", f"core.hooksPath={no_hooks.as_posix()}", *args], check=True,
                          capture_output=True, text=True, env=_git_env()).stdout.strip()


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
    prompt.write_text(PROMPT, encoding="utf-8")
    return {"root": root, "base": base, "head": head, "prompt": prompt, "tmp": tmp_path}


def _args(repo: dict, out: Path) -> list[str]:
    return ["--base", repo["base"], "--head", repo["head"], "--prompt", str(repo["prompt"]), "--out", str(out)]


def _run(repo: dict, *extra: str, key: str | None = None, out: Path | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    if key is not None:
        env["OPENROUTER_API_KEY"] = key
    args = _args(repo, out or repo["tmp"] / "out")
    return subprocess.run([sys.executable, "-I", "-c", _GUARD, str(SCRIPT), *args, *extra], cwd=repo["root"],
                          env=env, capture_output=True, text=True, timeout=120)


def _response(tmp: Path, content: str = MARKED, finish: str = "stop", cost: object = 0.0123, **over) -> Path:
    usage: dict = {"prompt_tokens": 1000, "completion_tokens": 200}
    if cost is not None:
        usage["cost"] = cost
    body = {"id": "gen-1", "model": "google/gemini-4-argon-20261013", "usage": usage,
            "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}], **over}
    body = {k: v for k, v in body.items() if v is not None}
    path = tmp / "saved_response.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _meta(out: Path) -> dict:
    return json.loads((out / "meta.json").read_text(encoding="utf-8"))


# --- refusals before anything is sent ----------------------------------------------------------------------------

def test_live_mode_without_a_key_is_refused_before_a_run_directory_exists(repo: dict) -> None:
    # Through the guard too: a regression that moved the key check after sending would meet it, not the API.
    out = repo["tmp"] / "out"
    result = _run(repo)
    assert result.returncode == 3, result.stderr
    assert "OPENROUTER_API_KEY" in result.stderr
    assert not out.exists()


def test_a_sha_that_is_not_a_commit_is_refused(repo: dict) -> None:
    repo = {**repo, "head": "0123456789abcdef0123456789abcdef01234567"}
    result = _run(repo, "--dry-run")
    assert result.returncode == 3, result.stderr
    assert "does not resolve to a commit" in result.stderr
    assert not (repo["tmp"] / "out").exists()


@pytest.mark.parametrize("paths", [[], ["docs/"]], ids=["same-sha", "paths-match-nothing"])
def test_an_empty_range_is_refused(repo: dict, paths: list[str]) -> None:
    over = {} if paths else {"head": repo["base"]}
    result = _run({**repo, **over}, "--dry-run", *(["--paths", *paths] if paths else []))
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "has no diff" in result.stderr
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


def test_the_versioned_env_templates_pass_and_a_real_env_beside_them_is_still_refused(repo: dict) -> None:
    templates = {".env.example": "A=1\n", "admin-ui/.env.local.example": "B=2\n", "ui/v2/.env.example": "C=3\n"}
    head = _commit(repo["root"], templates, "templates")
    result = _run({**repo, "head": head}, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert set(templates) <= set(_meta(repo["tmp"] / "out")["files_included"])
    for number, real in enumerate((".env", "admin-ui/.env.local", "secrets/x.example")):
        with_real = _commit(repo["root"], {real: "REAL=1\n"}, f"real {real}")
        refused = _run({**repo, "head": with_real}, "--dry-run", out=repo["tmp"] / f"out-{number}")
        assert refused.returncode == 3, (real, refused.stderr)
        assert "look like secrets" in refused.stderr and real in refused.stderr
        _git(repo["root"], "rm", "-q", real)  # gone from the next range: each round judges one path
        _git(repo["root"], "commit", "-q", "-m", f"drop {real}")


def test_a_payload_that_contains_the_key_is_refused(repo: dict) -> None:
    head = _commit(repo["root"], {"app/leak.py": f"KEY = '{KEY}'\n"}, "leak")
    result = _run({**repo, "head": head}, "--dry-run", key=KEY)
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert KEY not in result.stdout + result.stderr
    assert not (repo["tmp"] / "out").exists()


def test_an_out_directory_holding_a_previous_run_is_refused(repo: dict) -> None:
    out = repo["tmp"] / "out"
    out.mkdir()
    (out / "final.md").write_text("STALE\nREVIEW-COMPLETE: YES\n", encoding="utf-8")
    result = _run(repo, "--verify-response", str(_response(repo["tmp"])))
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "not an empty directory" in result.stderr
    assert (out / "final.md").read_text(encoding="utf-8") == "STALE\nREVIEW-COMPLETE: YES\n"


# --- what is sent ------------------------------------------------------------------------------------------------

def test_dry_run_writes_the_payload_and_makes_no_call_without_a_key(repo: dict) -> None:
    out = repo["tmp"] / "out"
    result = _run(repo, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == ["meta.json", "payload.json", "progress.log", "prompt.txt"]
    body = json.loads((out / "payload.json").read_text(encoding="utf-8"))
    assert body["model"] == "google/gemini-4-argon" and body["temperature"] == 0 and body["usage"] == {"include": True}
    text = body["messages"][0]["content"]
    assert text.startswith(PROMPT.rstrip("\n"))
    assert "+print('COMMITTED-HEAD')" in text and "-print('base')" in text  # the diff
    assert "### app/b.py" in text and "head change" in text  # a full file, the commit list
    meta = _meta(out)
    assert meta["status"] == "DRY-RUN" and meta["payload_chars"] == len(text)
    assert meta["base"] == repo["base"] and meta["head"] == repo["head"]
    assert (out / "prompt.txt").read_text(encoding="utf-8") == PROMPT


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
    ("kind", "over", "reason"),
    [("length", {"finish": "length"}, "finish_reason is 'length'"),
     ("empty content", {"content": "  \n"}, "empty content"),
     ("error body", {"choices": [], "error": {"code": 502, "message": "upstream"}}, "finish_reason is None"),
     ("another model", {"model": "openai/gpt-6-astra"}, "is not the requested"),
     ("a lookalike model", {"model": "google/gemini-4-argonite"}, "is not the requested"),
     ("no model", {"model": None}, "is not the requested")],
)
def test_an_incomplete_response_is_unverified(repo: dict, kind: str, over: dict, reason: str) -> None:
    result = _run(repo, "--verify-response", str(_response(repo["tmp"], **over)))
    assert result.returncode == 2, (kind, result.stderr)
    meta = _meta(repo["tmp"] / "out")
    assert meta["status"] == "UNVERIFIED" and reason in meta["reason"], (kind, meta["reason"])


def test_a_response_that_is_not_json_is_unverified(repo: dict) -> None:
    saved = repo["tmp"] / "truncated.json"
    saved.write_text('{"id": "gen-1", "choices": [{"finish_reason": "stop", "message": {"content": "REVIEW-COMPLETE',
                     encoding="utf-8")
    result = _run(repo, "--verify-response", str(saved))
    assert result.returncode == 2, result.stderr
    assert _meta(repo["tmp"] / "out")["reason"] == "response is not a JSON object"


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
        text = path.read_text(encoding="utf-8")
        # Not even redacted: the key never reaches the content; the redaction is a second wall.
        assert KEY not in text and "<redacted>" not in text, path.name
    assert KEY not in result.stdout + result.stderr


# --- the live path ------------------------------------------------------------------------------------------------

def test_the_guard_is_not_vacuous_a_live_call_meets_it_and_is_unverified(repo: dict) -> None:
    out = repo["tmp"] / "out"
    result = _run(repo, key=KEY)
    assert result.returncode == 2, (result.returncode, result.stderr)
    meta = _meta(out)
    assert meta["mode"] == "live" and meta["status"] == "UNVERIFIED" and "transport error" in meta["reason"]
    assert "NETWORK-BLOCKED" in (out / "response.json").read_text(encoding="utf-8")
    assert (out / "final.md").read_text(encoding="utf-8") == ""
    assert KEY not in result.stdout + result.stderr


def _load_module():
    spec = importlib.util.spec_from_file_location("argon_review_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live(module, repo: dict, monkeypatch: pytest.MonkeyPatch, post) -> tuple[int, Path]:
    monkeypatch.setattr(module, "post", post)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.chdir(repo["root"])
    out = repo["tmp"] / "out"
    return module.main(_args(repo, out)), out


def test_the_live_path_sends_one_request_and_keeps_the_key_out_of_every_file(
    repo: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, calls = _load_module(), []

    def fake_post(body: dict, key: str, timeout: float) -> tuple[int, str]:
        calls.append((body, key))
        return 200, _response(repo["tmp"]).read_text(encoding="utf-8")

    code, out = _live(module, repo, monkeypatch, fake_post)
    assert code == 0
    assert len(calls) == 1 and calls[0][1] == KEY and calls[0][0]["temperature"] == 0
    assert KEY not in json.dumps(calls[0][0])  # the key travels only as the header argument
    assert _meta(out)["mode"] == "live"
    for path in out.iterdir():
        text = path.read_text(encoding="utf-8")
        assert KEY not in text and "<redacted>" not in text, path.name


def test_a_transport_failure_on_the_live_path_is_unverified(repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_post(body: dict, key: str, timeout: float) -> tuple[int, str]:
        raise urllib.error.URLError(f"refused while sending {key}")

    code, out = _live(_load_module(), repo, monkeypatch, failing_post)
    assert code == 2
    meta = _meta(out)
    assert meta["status"] == "UNVERIFIED" and "transport error" in meta["reason"]
    response = (out / "response.json").read_text(encoding="utf-8")
    assert KEY not in response and "<redacted>" in response  # the redaction did the work


def test_a_rate_limited_live_call_is_unverified_and_not_retried(repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def limited_post(body: dict, key: str, timeout: float) -> tuple[int, str]:
        calls.append(1)
        return 429, json.dumps({"error": {"code": 429, "message": "rate limited"}})

    code, out = _live(_load_module(), repo, monkeypatch, limited_post)
    assert code == 2 and len(calls) == 1
    assert _meta(out)["reason"] == "HTTP 429"


def test_a_redirect_is_not_followed_and_the_key_goes_nowhere_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real `post`, with a handler in front of urllib's HTTPS handler that answers 302 without a socket.

    urllib's default follows a 301/302/303 of a POST as a GET and carries the `Authorization` header along.
    """
    module, seen = _load_module(), []

    def blocked(*args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("NETWORK-BLOCKED by the 040 test guard")

    for name in ("getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(module, "API_URL", "https://127.0.0.1:9/api/v1/chat/completions")

    class Answers302(urllib.request.BaseHandler):
        handler_order = 100  # before the real HTTPSHandler (500)

        def https_open(self, request: urllib.request.Request) -> urllib.response.addinfourl:
            seen.append((request.full_url, request.get_header("Authorization")))
            headers = http.client.HTTPMessage()
            headers["Location"] = "https://elsewhere.invalid/collect"
            response = urllib.response.addinfourl(io.BytesIO(b"moved"), headers, request.full_url, code=302)
            response.msg = "Found"
            return response

    real_build_opener = urllib.request.build_opener
    monkeypatch.setattr(module.urllib.request, "build_opener",
                        lambda *handlers: real_build_opener(*handlers, Answers302))
    status, raw = module.post({"model": "m"}, KEY, 5)
    assert (status, raw) == (302, "moved")
    assert seen == [("https://127.0.0.1:9/api/v1/chat/completions", f"Bearer {KEY}")]


# --- fix-delta of the external review of 3dad3761 (Codex, 2026-10-16): counterexamples ---------------------------

def _git_in(repo: Path, text: str, *args: str) -> str:
    """Bytes on stdin: a text-mode pipe would turn LF into CRLF on Windows and corrupt object text."""
    return subprocess.run(["git", "-C", str(repo), *args], input=text.encode("utf-8"), check=True,
                          capture_output=True, env=_git_env()).stdout.decode("utf-8").strip()


def _hash_blob(repo: Path, text: str) -> str:
    return _git_in(repo, text, "hash-object", "-w", "--stdin")


def test_f1_repository_config_cannot_run_a_signature_helper_that_sees_the_key(repo: dict) -> None:
    """`log.showSignature` + a signed commit made `git log` run `gpg.program` with the key in its environment."""
    canary = repo["tmp"] / "helper-ran.txt"
    helper = repo["tmp"] / "fake-gpg"
    helper.write_text(f'#!/bin/sh\nprintf "%s" "${{OPENROUTER_API_KEY:-NO-KEY}}" > "{canary.as_posix()}"\nexit 1\n',
                      encoding="utf-8", newline="\n")
    helper.chmod(0o755)
    root = repo["root"]
    _git(root, "config", "gpg.program", helper.as_posix())
    _git(root, "config", "log.showSignature", "true")
    unsigned = _git(root, "cat-file", "commit", repo["head"])
    header, message = unsigned.split("\n\n", 1)
    signature = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n -----END PGP SIGNATURE-----"
    signed = _git_in(root, f"{header}\n{signature}\n\n{message}\n", "hash-object", "-t", "commit", "-w", "--stdin")
    result = _run({**repo, "head": signed}, "--dry-run", key=KEY)
    assert result.returncode == 0, result.stderr
    assert not canary.exists(), f"a helper ran and saw {canary.read_text(encoding='utf-8')!r}"


def test_f1_every_git_child_runs_without_the_key_and_with_the_isolated_environment(
    repo: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, envs = _load_module(), []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        envs.append((args, kwargs.get("env")))
        return real_run(args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", recording_run)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.chdir(repo["root"])
    assert module.main([*_args(repo, repo["tmp"] / "out"), "--dry-run"]) == 0
    assert envs and all(args[0] == "git" for args, _ in envs)
    for args, env in envs:
        assert env is not None and "OPENROUTER_API_KEY" not in env, args
        assert (env["GIT_NO_REPLACE_OBJECTS"], env["GIT_CONFIG_NOSYSTEM"]) == ("1", "1"), args
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_PAGER"] == "cat", args
        assert "log" not in args and "show" not in args, args  # plumbing only


def test_f2_a_replacement_ref_cannot_swap_the_content_of_a_frozen_blob(repo: dict) -> None:
    root = repo["root"]
    original = _git(root, "rev-parse", f"{repo['head']}:app/b.py")
    _git(root, "replace", original, _hash_blob(root, "REPLACED-SECRET = 1\n"))
    result = _run(repo, "--dry-run")
    assert result.returncode == 0, result.stderr
    payload = (repo["tmp"] / "out" / "payload.json").read_text(encoding="utf-8")
    assert "REPLACED-SECRET" not in payload
    assert "B = 1" in payload


@pytest.mark.parametrize("paths", [["vendor/lib"], []], ids=["paths-the-gitlink", "whole-range"])
def test_f3_a_gitlink_is_refused_and_never_expanded(repo: dict, paths: list[str]) -> None:
    root = repo["root"]
    blob = _hash_blob(root, "GITLINK-TARGET-SECRET\n")
    tree = _git_in(root, f"100644 blob {blob}\thidden.txt\n", "mktree")
    target = _git(root, "commit-tree", tree, "-m", "outside the range")
    _git(root, "update-index", "--add", "--cacheinfo", f"160000,{target},vendor/lib")
    _git(root, "commit", "-q", "-m", "gitlink")
    head = _git(root, "rev-parse", "HEAD")
    result = _run({**repo, "head": head}, "--dry-run", *(["--paths", *paths] if paths else []))
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "gitlink" in result.stderr and "vendor/lib" in result.stderr
    assert not (repo["tmp"] / "out").exists()


@pytest.mark.parametrize(
    "content",
    ["VERDICT-040-A: \nCLASS-1-COUNT: 0\nREVIEW-COMPLETE: YES\n",
     "VERDICT-040-A: SOUND\nCLASS-1-COUNT: 0unknown\nREVIEW-COMPLETE: YES\n",
     "VERDICT-040-A: SOUND\nCLASS-1-COUNT: 0\nREVIEW-COMPLETE: YES—but only the first half was reviewed\n",
     "VERDICT-040-A: GREAT\nCLASS-1-COUNT: 0\nREVIEW-COMPLETE: YES\n",
     "VERDICT-040-A: SOUND but see below\nCLASS-1-COUNT: 0\nREVIEW-COMPLETE: YES\n",
     "VERDICT-040-A: SOUND\nVERDICT-040-B: \nCLASS-1-COUNT: 0\nREVIEW-COMPLETE: YES\n"],
    ids=["empty-verdict", "count-not-a-number", "complete-qualified", "verdict-not-declared", "verdict-with-tail",
         "second-verdict-empty"],
)
def test_f4_a_malformed_marker_is_unverified(repo: dict, content: str) -> None:
    result = _run(repo, "--verify-response", str(_response(repo["tmp"], content=content)))
    assert result.returncode == 2, (result.returncode, result.stderr)
    assert "marker" in _meta(repo["tmp"] / "out")["reason"]


def test_f4_a_prompt_that_declares_no_verdict_values_is_refused(repo: dict) -> None:
    repo["prompt"].write_text("Review this, no markers declared.\n", encoding="utf-8")
    result = _run(repo, "--dry-run")
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "declares no verdict values" in result.stderr


@pytest.mark.parametrize("model", ["google/gemini-4-argon-evil", "google/gemini-4-argon-", "google/gemini-4-argon-20261014"])
def test_f5_a_model_outside_the_allowlist_is_unverified(repo: dict, model: str) -> None:
    result = _run(repo, "--verify-response", str(_response(repo["tmp"], model=model)))
    assert result.returncode == 2, (model, result.returncode, result.stderr)
    assert "is not the requested" in _meta(repo["tmp"] / "out")["reason"]


def test_f5_the_requested_id_itself_is_accepted(repo: dict) -> None:
    result = _run(repo, "--verify-response", str(_response(repo["tmp"], model="google/gemini-4-argon")))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tail", ["\n", "\r"], ids=["newline", "carriage-return"])
def test_f6_a_key_that_is_not_header_safe_is_refused_without_echoing_it(repo: dict, tail: str) -> None:
    result = _run(repo, key=KEY + tail)
    assert result.returncode == 3, (result.returncode, result.stderr[-400:].replace(KEY, "<KEY>"))
    assert KEY not in result.stdout + result.stderr
    assert "header-safe" in result.stderr
    assert not (repo["tmp"] / "out").exists()


def test_a_response_over_the_read_cap_is_unverified(repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()

    def blocked(*args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("NETWORK-BLOCKED by the 040 test guard")

    for name in ("getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 100)
    monkeypatch.setattr(module, "API_URL", "https://127.0.0.1:9/api/v1/chat/completions")

    class AnswersBig(urllib.request.BaseHandler):
        handler_order = 100

        def https_open(self, request: urllib.request.Request) -> urllib.response.addinfourl:
            body = _response(repo["tmp"]).read_bytes() + b" " * 1000
            response = urllib.response.addinfourl(io.BytesIO(body), http.client.HTTPMessage(), request.full_url,
                                                  code=200)
            response.msg = "OK"
            return response

    real_build_opener = urllib.request.build_opener
    monkeypatch.setattr(module.urllib.request, "build_opener",
                        lambda *handlers: real_build_opener(*handlers, AnswersBig))
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.chdir(repo["root"])
    out = repo["tmp"] / "out"
    assert module.main(_args(repo, out)) == 2
    assert "MAX_RESPONSE_BYTES" in (out / "response.json").read_text(encoding="utf-8")
