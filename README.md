# sismos-azuero

Historial de sismos en azuero con mapas y archivos multimedia

## Inicio rápido

```bash
make setup     # entorno + dependencias (uv)
make test      # tests con cobertura
make format    # ruff: formato + orden de imports
make diagrams  # UML con pyreverse (necesita graphviz)
make help      # lista todos los comandos
```

## Estructura

```
src/sismos_azuero/   código del paquete
tests/unit/              tests unitarios
tests/integration/       tests de integración
logs/                    logs en runtime (no se versiona)
docs/diagrams/           diagramas generados
```

## Logging

Cada módulo usa `logger = logging.getLogger(__name__)`.
`setup_logging()` se llama **una sola vez**, en el punto de entrada.
