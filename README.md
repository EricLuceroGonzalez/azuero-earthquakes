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

## Video de sismos con sonido

Dibuja los sismos del catálogo del USGS en Panamá en orden cronológico. Cada sismo suena
al aparecer, con un sonido de ElevenLabs que depende de su magnitud. Necesitas `ffmpeg`
en el `PATH`.

```bash
cp .env.example .env   # y pon tu ELEVENLABS_API_KEY
make fetch             # catálogo USGS -> data/sismos.csv
make sounds            # banco de sonidos -> sounds/*.mp3 (versionar: son tuyos)
make preview           # prueba rápida -> out/preview.mp4
make render            # video completo -> out/sismos_panama.mp4
```

Para pasar opciones: `uv run python -m sismos_azuero render --help`.

- **Tiempo:** la fecha real se mapea de forma lineal al video. Los primeros años se ven
  escasos porque la red sísmica tenía menos estaciones.
- **Tamaño:** el área del círculo crece como `10^(0.45·(M−2.5))`.
- **Color:** la profundidad, en una rampa de un solo tono (claro = superficial).
- **Sonido:** seis categorías de magnitud con tres variaciones cada una. El volumen sube
  6,5 dB por grado y un limitador suave evita la saturación.
- **Mapa base:** `global-land-mask` (GLOBE, ~1 km), sin conexión y sin claves.
- **Limitaciones:** el catálogo USGS solo está completo desde M≈4 en Panamá. La traza de
  la falla de Pedro Miguel es aproximada.

## Estructura

```
src/sismos_azuero/   código del paquete
data/                    catálogo de sismos (data/sismos.csv)
sounds/                  banco de sonidos generado con ElevenLabs
out/                     videos generados (no se versiona)
tests/unit/              tests unitarios
tests/integration/       tests de integración
logs/                    logs en runtime (no se versiona)
docs/diagrams/           diagramas generados
```

## Logging

Cada módulo usa `logger = logging.getLogger(__name__)`.
`setup_logging()` se llama **una sola vez**, en el punto de entrada.
