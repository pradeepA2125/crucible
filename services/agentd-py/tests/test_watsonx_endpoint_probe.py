from __future__ import annotations

import asyncio
import os
import socket
from urllib.parse import urlparse

import httpx
import pytest

from agentd.providers.watsonx_transport import WatsonxJsonTransport


def _env_ready() -> bool:
    return all(
        [
            bool(os.getenv("WATSONX_API_KEY")),
            bool(os.getenv("WATSONX_PROJECT_ID") or os.getenv("WATSONX_SPACE_ID")),
            bool(os.getenv("WATSONX_URL")),
        ]
    )


async def _run_probe() -> dict[str, object]:
    url = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
    host = urlparse(url).hostname
    if not host:
        raise RuntimeError(f"Invalid WATSONX_URL: {url}")

    socket.gethostbyname(host)

    # Reachability check; any HTTP response means endpoint is reachable.
    response = httpx.get(url, timeout=20.0, follow_redirects=True)
    if response.status_code >= 500:
        raise RuntimeError(
            f"Watsonx endpoint reachable but unhealthy: HTTP {response.status_code}"
        )

    transport = WatsonxJsonTransport(
        api_key=os.getenv("WATSONX_API_KEY"),
        project_id=os.getenv("WATSONX_PROJECT_ID"),
        url=url,
        space_id=os.getenv("WATSONX_SPACE_ID"),
    )
    model = os.getenv("AI_EDITOR_WATSONX_MODEL", "ibm/granite-3-8b-instruct")
    return await transport.generate_json(
        model=model,
        schema_name="watsonx_probe",
        schema={
            "type": "object",
            "properties": {"pong": {"type": "string"}},
            "required": ["pong"],
            "additionalProperties": False,
        },
        system_instructions="Return strict JSON with pong='ok'.",
        user_payload={"probe": "endpoint verification"},
    )


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_WATSONX_ENDPOINT_TEST") != "1" or not _env_ready(),
    reason=(
        "Set RUN_WATSONX_ENDPOINT_TEST=1 and export "
        "WATSONX_API_KEY + (WATSONX_PROJECT_ID or WATSONX_SPACE_ID) + WATSONX_URL"
    ),
)


@pytest.mark.asyncio
async def test_watsonx_endpoint_probe() -> None:
    payload = await _run_probe()
    assert isinstance(payload, dict)
    assert "pong" in payload
    assert isinstance(payload["pong"], str)


if __name__ == "__main__":
    result = asyncio.run(_run_probe())
    print(result)
