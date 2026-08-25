# Team — granja de modelos orquestada por Claude

Claude (Desktop/Code) actúa **solo** como orquestador: planifica y delega. Toda la
implementación —código, análisis de logs, revisión— la ejecuta una granja de
modelos gratuitos/de suscripción a través de un servidor MCP local que habla
con un gateway LiteLLM 24/7.

Ver el plan completo de diseño en `.claude/plans` (o pedir un resumen).

## Estado

- **Gateway LiteLLM**: desplegado en `203.0.113.10:4000` (Docker: litellm + postgres + redis).
  UI en `http://203.0.113.10:4000/ui`.
- **Servidor MCP**: registrado globalmente en Claude Code (`claude mcp add team --scope user`),
  disponible en cualquier proyecto, no solo este repo.
- **Fases 1-4 y 6-7 completas y verificadas en vivo** contra el gateway real (ver
  `.claude/plans` para el detalle): los 4 tiers responden, `team_task` y los 4
  `kind` de `team_feature` (`new`/`refactor`/`fix`/`review`) producen código
  real con tests en verde, `team_ask` tiene map-reduce con verificación de
  citas + búsqueda web opcional (Tavily, vía MCP Gateway de LiteLLM).
- **Pendiente (Fase 5)**: `team_epic`, `team_validate`, `selftest` siguen como
  stubs.

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

## Registrar el MCP en Claude Code (global, cualquier proyecto)

```bash
claude mcp add team --scope user -- "C:\Users\Kire\Documents\GitHub\Team\.venv\Scripts\python.exe" -m team_mcp.server
claude mcp list   # debe mostrar "team ... Connected"
```

Importante: la ruta al intérprete debe ser la del `.venv` de este repo
(no `python` a secas) — es donde está instalado el paquete `team_mcp` en
modo editable. `.env` se carga siempre desde la raíz de este repo
(`config.py::load_env`), sin importar desde qué proyecto se invoque el
servidor.

Para Claude Desktop, el equivalente manual en su config JSON:
```json
{
  "mcpServers": {
    "team": {
      "command": "C:\\Users\\Kire\\Documents\\GitHub\\Team\\.venv\\Scripts\\python.exe",
      "args": ["-m", "team_mcp.server"]
    }
  }
}
```

## Verificación rápida

```bash
curl http://203.0.113.10:4000/health/liveliness
```
