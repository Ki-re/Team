# Convención de knowledge-base para `update_docs`

Estándar que `docs_sync` (el subagente de documentación de `team_feature`/
`team_epic`, ver Fase 12 del plan) espera de un `kb_path`. No es un formato
nuevo inventado para este proyecto — es, deliberadamente, el mismo patrón
que usa el propio sistema de memoria de Claude Code: un archivo por tema
con frontmatter, más un índice liviano que se lee siempre primero. Si tu
KB ya sigue esta forma (aunque no la conocieras por este nombre), no hay
nada que migrar.

## Por qué esta forma y no otra

- **Markdown plano, sin vector DB ni embeddings.** Para el tamaño típico de
  un KB de proyecto (decenas-cientos de archivos), un índice + frontmatter
  filtra por relevancia igual de bien que una búsqueda semántica, sin
  añadir infraestructura (embedding model, vector store) que este proyecto
  evita en todos lados por principio.
- **Un archivo por tema, no un único documento gigante.** Permite que
  `docs_sync` (o cualquier sesión de Claude) decida qué leer sin cargar
  todo el KB en contexto — exactamente el problema que sufrió el agente
  que motivó la Fase 12: sin un índice barato, la única opción era leer
  código para reconstruir el contexto a mano.
- **`INDEX.md` es la puerta de entrada, no una copia del contenido.** Una
  línea por archivo, no un resumen — si el índice duplica el contenido,
  las dos copias se desincronizan con el tiempo.

## Estructura

```
mi-proyecto/
└─ knowledge-base/       # o el nombre/ubicación que prefieras
   ├─ INDEX.md           # obligatorio — sin esto, docs_sync no hace nada
   ├─ arquitectura.md
   ├─ decisiones/
   │  └─ por-que-postgres.md
   └─ adaptadores/
      └─ portal-x.md
```

El KB puede vivir como carpeta dentro del propio repo, o como repo
dedicado aparte en la organización (útil cuando varios repos comparten el
mismo KB). Para `docs_sync`, `kb_path` es solo una ruta local — si es un
repo dedicado, su clon local debe estar en `TEAM_SANDBOX_ROOTS` (ver
`.env.example`) o el sandbox rechazará cualquier escritura ahí. Si el KB
vive en su propio repo, sus cambios quedan como working tree sin
commitear en ESE repo — `docs_sync` nunca hace commits por su cuenta, ni
ahí ni en el proyecto principal; el commit lo decide quien orquesta.

## `INDEX.md`

Una línea por archivo de tema, igual que `MEMORY.md` en el sistema de
memoria de Claude:

```markdown
- [Arquitectura general](arquitectura.md) — visión de alto nivel del sistema y sus componentes.
- [Por qué Postgres](decisiones/por-que-postgres.md) — decisión y alternativas descartadas.
- [Adaptador Portal X](adaptadores/portal-x.md) — mapeo de campos y limitaciones conocidas.
```

## Frontmatter de cada archivo de tema

```markdown
---
name: arquitectura-general
description: Visión de alto nivel del sistema y sus componentes principales.
tags: [arquitectura, overview]
last_verified: 2026-08-27
---

Contenido normal en markdown...
```

- `name` — slug corto, único dentro del KB.
- `description` — una frase; es lo que `docs_sync` lee para decidir
  relevancia SIN abrir el archivo entero.
- `tags` — opcional, lista corta.
- `last_verified` — fecha ISO (`YYYY-MM-DD`) de la última vez que un
  humano o una sesión de Claude confirmó que el contenido sigue siendo
  correcto. `team_validate` avisa (no bloquea) cuando pasa de ~180 días.

Un archivo `.md` sin frontmatter no rompe nada, pero `docs_sync` no lo
indexa (no aparece en el resumen barato que se le pasa al modelo) y
`team_validate` no puede evaluar su staleness.

## Qué hace `docs_sync` con esto

Dos pasadas, no una — la primera versión intentaba pedir la edición
directamente sobre el índice (solo nombre+descripción, sin cuerpo) y
fallaba en la primera prueba real: el modelo no tiene forma de copiar un
`search` exacto de un texto que nunca vio.

1. **Selección barata**: lee `INDEX.md` + el frontmatter de cada archivo
   de tema (nunca el cuerpo completo en este paso) y le pregunta a
   `tier-context`, con el resumen del cambio recién aplicado, cuáles del
   índice probablemente quedaron desactualizados.
2. **Edición dirigida**: por cada archivo seleccionado, una llamada aparte
   con su contenido REAL completo, pidiendo un bloque search/replace
   exacto — mismo patrón que usa `team_task` para ediciones de código, con
   un reintento si el `search` no encaja, realimentando el error literal.
3. Cada edición se valida de forma determinista antes de aplicarse: la
   ruta debe quedar dentro de `kb_path`, el frontmatter resultante debe
   seguir siendo YAML válido, y ningún link relativo nuevo puede quedar
   roto.
4. Se aplica vía el mismo `Sandbox` que cualquier otro edit del proyecto
   — respeta `TEAM_DRY_RUN` y la whitelist de rutas igual que siempre.

**Alcance de esta versión: solo actualiza archivos que YA están en el
índice.** No crea entradas nuevas — decidir dónde debería vivir un doc
nuevo y con qué estructura es un juicio que de momento se deja a quien
orquesta (Claude, o un humano), no a `docs_sync`. Si hace falta
automatizarlo también, es una ampliación futura, no algo que este primer
corte prometa.

## Cómo activarlo

```python
team_feature(
    spec="...",
    target_paths=["..."],
    update_docs=True,
    kb_path="knowledge-base",  # o la ruta absoluta al repo dedicado
)
```

Mismo parámetro en `team_epic`, pero se sincroniza una sola vez al final
de todo el plan, no por cada nodo.

`team_validate(scope="knowledge-base")` audita el KB en sí (frontmatter,
links rotos, staleness) sin necesidad de `update_docs` — funciona en
cualquier directorio con `INDEX.md`, lo haya tocado `docs_sync` o no.
