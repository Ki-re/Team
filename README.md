# Team — granja de modelos orquestada por Claude

Claude (Desktop/Code) actúa **solo** como orquestador: planifica y delega. Toda la
implementación —código, análisis de logs, revisión— la ejecuta una granja de
modelos gratuitos/de suscripción a través de un servidor MCP local que habla
con un gateway LiteLLM 24/7.

Ver el plan completo de diseño en `.claude/plans` (o pedir un resumen).

## Estado

- **Gateway LiteLLM**: desplegado en `203.0.113.10:4000` (Docker: litellm + postgres + redis).
  UI en `http://203.0.113.10:4000/ui`.
- **Servidor MCP**: en desarrollo. `team_task` es el primer entrypoint funcional.

## Estructura

```
deploy/            docker-compose.yml + litellm.config.yaml del gateway remoto
src/team_mcp/       servidor MCP (Python)
  providers/        gateway.py (LiteLLM HTTP) · agy.py (Antigravity CLI) · router.py
  engine/           verify · consensus · critic · repair · schemas · sandbox · ledger · cache
  workflows/         team_task · team_feature · team_epic · team_ask · team_validate
tests/golden/       casos para el pipeline selftest
```

## Setup local

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows
pip install -e ".[dev]"
cp .env.example .env        # rellenar TEAM_GATEWAY_KEY, TEAM_SANDBOX_ROOTS, etc.
```

## Setup del gateway (ya desplegado)

```bash
# desde deploy/, con .env relleno con las API keys de cada proveedor
scp docker-compose.yml litellm.config.yaml .env claude@203.0.113.10:~/team-gateway/
ssh claude@203.0.113.10 "cd ~/team-gateway && docker compose up -d"
```

Las API keys pueden añadirse de dos formas: editando `deploy/.env` y
redesplegando, o directamente desde la UI web (`/ui`) sin reiniciar nada.

**Nota de arranque**: el primer `docker compose up -d` de LiteLLM tarda
~3-4 minutos (migraciones Prisma + registro de ~8 modelos) en el servidor
actual (2 GB RAM). No es un cuelgue; `docker logs -f team-litellm` lo muestra.

## Registrar el MCP en Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "team": {
      "command": "python",
      "args": ["-m", "team_mcp.server"],
      "cwd": "C:\\Users\\Kire\\Documents\\GitHub\\Team"
    }
  }
}
```

## Verificación rápida

```bash
curl http://203.0.113.10:4000/health/liveliness
```
