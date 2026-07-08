from __future__ import annotations

import asyncio
import json
import os

try:
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
except ImportError:
    Credentials = None
    ModelInference = None
    GenParams = None

from agentd.providers.contracts import ModelJsonTransport


class WatsonxJsonTransport(ModelJsonTransport):
    """
    IBM watsonx.ai transport for foundation models.
    Requires WATSONX_API_KEY, WATSONX_PROJECT_ID, and WATSONX_URL.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        project_id: str | None = None,
        url: str | None = None,
        space_id: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("WATSONX_API_KEY")
        self.project_id = project_id or os.getenv("WATSONX_PROJECT_ID")
        self.url = url or os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
        self.space_id = space_id or os.getenv("WATSONX_SPACE_ID")

        if Credentials is None or ModelInference is None or GenParams is None:
            raise RuntimeError(
                "ibm-watsonx-ai package is required for WatsonxJsonTransport"
            )
        if not self.api_key:
            raise RuntimeError("WATSONX_API_KEY is required")
        if not self.project_id and not self.space_id:
            raise RuntimeError("WATSONX_PROJECT_ID or WATSONX_SPACE_ID is required")

        self.credentials = Credentials(url=self.url, api_key=self.api_key)

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
    ) -> dict[str, object]:
        """
        Generates JSON using watsonx.ai ModelInference.
        Extracts reasoning/thinking tokens if present.
        """
        prompt = (
            f"{system_instructions}\n\n"
            f"CONTEXT:\n{json.dumps(user_payload, indent=2)}\n\n"
            f"You MUST return a JSON object that strictly follows this schema:\n"
            f"{json.dumps(schema, indent=2)}\n\n"
            f"If you use reasoning, wrap it in <think> tags before the JSON.\n\n"
            f"OUTPUT:"
        )

        if GenParams is None:
            msg = "ibm-watsonx-ai package is required for WatsonxJsonTransport"
            raise RuntimeError(msg)

        params = {
            GenParams.DECODING_METHOD: "greedy",
            GenParams.MAX_NEW_TOKENS: 8192,  # Increased to allow for reasoning + large JSON
            GenParams.MIN_NEW_TOKENS: 1,
        }

        model_inference = ModelInference(
            model_id=model,
            params=params,
            credentials=self.credentials,
            project_id=self.project_id,
            space_id=self.space_id,
        )

        # Watsonx ModelInference is synchronous in the base SDK
        response = await asyncio.to_thread(model_inference.generate_text, prompt=prompt)
        
        # Extract thinking if present
        thinking = ""
        if "<think>" in response and "</think>" in response:
            start = response.find("<think>") + 7
            end = response.find("</think>")
            thinking = response[start:end].strip()
            # Clean up the response for JSON parsing
            response = response[end + 8 :].strip()
        elif "<think>" in response:
            # Handle cases where it might not close the tag but we still want the text
            start = response.find("<think>") + 7
            thinking = response[start:].strip()
            response = "" # Likely failed to generate JSON if it's all thinking

        if thinking:
            # Log thinking to a debug file for transparency
            log_dir = os.getenv("CRUCIBLE_LOG_DIR", ".tmp/reasoning")
            os.makedirs(log_dir, exist_ok=True)
            with open(f"{log_dir}/watsonx_thinking.log", "a") as f:
                f.write(f"--- MODEL: {model} ---\n{thinking}\n\n")

        return self._parse_output_object(response)

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> str:
        """Generates raw text using watsonx.ai ModelInference."""
        prompt = (
            f"{system_instructions}\n\n"
            f"CONTEXT:\n{json.dumps(user_payload, indent=2)}\n\n"
            f"OUTPUT:"
        )

        if GenParams is None:
            msg = "ibm-watsonx-ai package is required for WatsonxJsonTransport"
            raise RuntimeError(msg)

        params = {
            GenParams.DECODING_METHOD: "greedy",
            GenParams.MAX_NEW_TOKENS: 4096,
            GenParams.MIN_NEW_TOKENS: 1,
        }

        model_inference = ModelInference(
            model_id=model,
            params=params,
            credentials=self.credentials,
            project_id=self.project_id,
            space_id=self.space_id,
        )

        # Watsonx ModelInference is synchronous in the base SDK
        response = await asyncio.to_thread(model_inference.generate_text, prompt=prompt)
        return response.strip()

    def _parse_output_object(self, output_text: str) -> dict[str, object]:
        # Strip potential markdown fences and other noise
        raw = output_text.strip()
        
        # Look for the first '{' and last '}' to isolate the JSON object
        start_idx = raw.find("{")
        end_idx = raw.rfind("}")
        
        if start_idx != -1 and end_idx != -1:
            raw = raw[start_idx : end_idx + 1]
        
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            msg = f"Watsonx output is not valid JSON: {output_text[:500]}"
            raise RuntimeError(msg) from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Watsonx output must be a JSON object")

        return payload
