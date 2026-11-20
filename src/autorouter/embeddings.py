"""Opt-in OpenAI-compatible embeddings. No request occurs unless enabled."""

import json, math, os, urllib.parse, urllib.request


class Embeddings:
    def __init__(self, config):
        self.config = config
        self.model = config.get("model", "text-embedding-3-small")
        provider = config.get("provider", "local")
        base = config.get(
            "base_url",
            "http://127.0.0.1:11434/v1"
            if provider == "local"
            else "https://api.openai.com/v1",
        ).rstrip("/")
        host = urllib.parse.urlparse(base).hostname
        if provider == "local" and host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Local embeddings must use a loopback endpoint")
        if provider != "local" and not config.get("allow_hosted", False):
            raise ValueError(
                "Hosted embeddings require explicit allow_hosted=true; billed separately from ChatGPT"
            )
        if provider != "local" and not base.startswith("https://"):
            raise ValueError("Hosted embeddings require HTTPS")
        self.url = base + "/embeddings"
        key_name = config.get("api_key_env") or ("OPENAI_API_KEY" if provider != "local" else None)
        self.key = os.environ.get(key_name) if key_name else None
        if provider != "local" and not self.key:
            raise ValueError(
                "Configured embedding API key environment variable is missing"
            )
        self.identity = base + "|" + self.model

    def embed(self, text):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        request = urllib.request.Request(
            self.url,
            json.dumps({"model": self.model, "input": text[:24000]}).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            vector = json.load(response)["data"][0]["embedding"]
        if not vector or not all(
            isinstance(x, (float, int)) and math.isfinite(x) for x in vector
        ):
            raise ValueError("Invalid embedding response")
        return vector


def cosine(a, b):
    if len(a) != len(b):
        return 0
    norm = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0
