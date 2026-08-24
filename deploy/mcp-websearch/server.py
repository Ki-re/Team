"""Servidor MCP mínimo: una sola tool, `web_search`, sobre Brave Search API.

Registrado como upstream MCP server en el gateway LiteLLM (ver
deploy/litellm.config.yaml, sección `mcp_servers`). Solo lo usa
`tier-context` desde `team_ask` cuando Claude pasa `allow_web_search=True`
— ningún pipeline que escribe código toca esto (ver plan, Fase 7).

Corre con transporte streamable-http para que LiteLLM (otro contenedor en
la misma red docker) pueda alcanzarlo por HTTP; no tiene sentido stdio
aquí, ya que no hay un proceso padre local que lo lance como subprocess.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.mcpserver import MCPServer

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

mcp = MCPServer(name="web_search")


@mcp.tool(
    description=(
        "Busca en la web y devuelve resultados (título, url, snippet). "
        "Usar cuando la pregunta necesita información externa actual "
        "(versión de una librería, documentación de una API externa, algo "
        "que no está en el código local)."
    )
)
async def web_search(query: str, count: int = 5) -> list[dict]:
    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY no configurada en el servidor mcp-websearch")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            BRAVE_ENDPOINT,
            params={"q": query, "count": min(count, 10)},
            headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("web", {}).get("results", [])
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
        }
        for r in results[:count]
    ]


def main() -> None:
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8090)


if __name__ == "__main__":
    main()
