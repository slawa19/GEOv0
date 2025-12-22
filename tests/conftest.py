"""
GEO Hub — pytest fixtures and configuration.

Provides:
- Deterministic key generation
- HTTP client with correlation headers
- Test database setup/teardown
- Artifact collection
"""
import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Generator

import pytest
import httpx

# =============================================================================
# Constants
# =============================================================================
TEST_SEED = os.environ.get("TEST_SEED", "2025-geo-test")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000/api/v1")


# =============================================================================
# Deterministic Key Generation
# =============================================================================
def deterministic_keypair(seed: str, index: int) -> tuple[bytes, bytes, str]:
    """
    Generate Ed25519-compatible key material deterministically.
    
    For real Ed25519 operations, use pynacl:
        from nacl.signing import SigningKey
        seed_bytes = hashlib.sha256(f"{seed}:{index}".encode()).digest()
        sk = SigningKey(seed_bytes)
        pk = sk.verify_key.encode()
    
    Args:
        seed: Base seed string
        index: Key index (0 for Alice, 1 for Bob, etc.)
    
    Returns:
        (private_key_bytes, public_key_bytes, pid_suffix)
    """
    material = f"{seed}:{index}".encode()
    private_key = hashlib.sha256(material).digest()
    public_key = hashlib.sha256(private_key).digest()
    pid_suffix = hashlib.sha256(public_key).hexdigest()[:16]
    return private_key, public_key, pid_suffix


# =============================================================================
# Session-level fixtures
# =============================================================================
@pytest.fixture(scope="session")
def run_id() -> str:
    """Unique identifier for this test run (pytest session)."""
    return str(uuid.uuid4())


@pytest.fixture(scope="session")
def artifacts_dir(run_id: str) -> Path:
    """Directory for storing test artifacts."""
    base = Path(__file__).parent / "artifacts" / run_id
    base.mkdir(parents=True, exist_ok=True)
    
    # Write run metadata
    meta = {
        "run_id": run_id,
        "started_at": datetime.utcnow().isoformat(),
        "seed": TEST_SEED,
        "api_base_url": API_BASE_URL,
    }
    import json
    (base / "meta.json").write_text(json.dumps(meta, indent=2))
    
    return base


# =============================================================================
# Test-level fixtures
# =============================================================================
@pytest.fixture
def scenario_id(request) -> str:
    """
    Get scenario ID from marker or derive from test name.
    
    Usage:
        @pytest.mark.scenario("TS-12")
        def test_payment_single_path():
            ...
    """
    marker = request.node.get_closest_marker("scenario")
    if marker and marker.args:
        return marker.args[0]
    # Derive from test name: test_ts_12_payment -> TS-12
    name = request.node.name
    if name.startswith("test_ts_"):
        parts = name.split("_")
        if len(parts) >= 3:
            return f"TS-{parts[2].upper()}"
    return name


@pytest.fixture
def request_id() -> str:
    """Unique identifier for HTTP request correlation."""
    return str(uuid.uuid4())


# =============================================================================
# HTTP Client
# =============================================================================
@pytest.fixture
def http_client(run_id: str, scenario_id: str) -> Generator[httpx.Client, None, None]:
    """
    HTTP client with correlation headers.
    
    Automatically adds:
    - X-Run-ID
    - X-Scenario-ID
    - X-Request-ID (generated per request)
    """
    def add_correlation_headers(request: httpx.Request) -> httpx.Request:
        request.headers["X-Run-ID"] = run_id
        request.headers["X-Scenario-ID"] = scenario_id
        if "X-Request-ID" not in request.headers:
            request.headers["X-Request-ID"] = str(uuid.uuid4())
        return request
    
    with httpx.Client(
        base_url=API_BASE_URL,
        timeout=30.0,
        event_hooks={"request": [add_correlation_headers]},
    ) as client:
        yield client


@pytest.fixture
async def async_http_client(
    run_id: str, scenario_id: str
) -> Generator[httpx.AsyncClient, None, None]:
    """Async version of HTTP client with correlation headers."""
    def add_correlation_headers(request: httpx.Request) -> httpx.Request:
        request.headers["X-Run-ID"] = run_id
        request.headers["X-Scenario-ID"] = scenario_id
        if "X-Request-ID" not in request.headers:
            request.headers["X-Request-ID"] = str(uuid.uuid4())
        return request
    
    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        timeout=30.0,
        event_hooks={"request": [add_correlation_headers]},
    ) as client:
        yield client


# =============================================================================
# Test Participants (deterministic)
# =============================================================================
@pytest.fixture
def alice_keys():
    """Alice's keypair (index 0)."""
    return deterministic_keypair(TEST_SEED, 0)


@pytest.fixture
def bob_keys():
    """Bob's keypair (index 1)."""
    return deterministic_keypair(TEST_SEED, 1)


@pytest.fixture
def carol_keys():
    """Carol's keypair (index 2)."""
    return deterministic_keypair(TEST_SEED, 2)


@pytest.fixture
def dave_keys():
    """Dave's keypair (index 3)."""
    return deterministic_keypair(TEST_SEED, 3)


# =============================================================================
# Test Database Reset
# =============================================================================
@pytest.fixture
def reset_state(http_client: httpx.Client):
    """
    Reset test state before test.
    
    Calls /_test/reset endpoint to clear DB and Redis.
    Only works in dev/test environment.
    """
    response = http_client.post("/_test/reset")
    if response.status_code == 403:
        pytest.skip("Test endpoints disabled (not in dev/test environment)")
    response.raise_for_status()
    return response.json()


# =============================================================================
# Artifact Collection
# =============================================================================
@pytest.fixture
def collect_artifacts(
    artifacts_dir: Path,
    scenario_id: str,
    http_client: httpx.Client,
):
    """
    Collect artifacts after test completion.
    
    Saves snapshot and events to artifacts directory.
    """
    yield  # Test runs here
    
    # After test: collect snapshot
    scenario_dir = artifacts_dir / scenario_id.replace("-", "_")
    scenario_dir.mkdir(exist_ok=True)
    
    try:
        response = http_client.get(
            "/_test/snapshot",
            params={"include_events": "true"},
        )
        if response.status_code == 200:
            import json
            data = response.json()
            (scenario_dir / "snapshot.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False)
            )
            
            # Extract events to JSONL
            if "data" in data and "events" in data["data"]:
                events = data["data"]["events"]
                with open(scenario_dir / "events.jsonl", "w") as f:
                    for event in events:
                        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        # Don't fail test due to artifact collection issues
        print(f"Warning: Failed to collect artifacts: {e}")
