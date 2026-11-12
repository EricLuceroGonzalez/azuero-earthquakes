"""Sismos de Panamá: mapa animado + sonificación por magnitud.

Pasos (cada uno guarda su resultado y se puede correr por separado):

    uv run python -m sismos_azuero fetch    # catálogo del USGS -> data/sismos.csv
    uv run python -m sismos_azuero sounds   # banco de sonidos (ElevenLabs) -> sounds/
    uv run python -m sismos_azuero render   # mapa + audio -> out/sismos_panama.mp4
    uv run python -m sismos_azuero all      # los tres en orden

La clave de ElevenLabs se lee de ELEVENLABS_API_KEY (solo hace falta en `sounds`).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# Raíz del proyecto (src/sismos_azuero/main.py -> ../../).
HERE = Path(__file__).resolve().parents[2]
DATA_DIR = HERE / "data"
SOUNDS_DIR = HERE / "sounds"
OUT_DIR = HERE / "out"

# Recuadro de Panamá (incluye la zona de fractura de Panamá y el sur de Azuero).
BBOX = dict(minlatitude=5.0, maxlatitude=10.5, minlongitude=-84.0, maxlongitude=-76.5)

# Regiones para el render: recortan el catálogo (que siempre cubre todo Panamá).
REGIONS = {
    "panama": dict(title="Sismos en Panamá", bbox=BBOX),
    "azuero": dict(
        title="Sismos en Azuero–Soná",
        bbox=dict(
            minlatitude=6.6, maxlatitude=8.4, minlongitude=-82.0, maxlongitude=-79.5
        ),
    ),
}
TZ = "America/Panama"
MESES = "ene feb mar abr may jun jul ago sep oct nov dic".split()

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
ELEVEN_URL = "https://api.elevenlabs.io/v1/sound-generation"

SAMPLE_RATE = 44100
DEFAULT_OUTPUT = "sismos_panama.mp4"

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Banco de sonidos: una categoría por rango de magnitud, con variaciones.
# Ajusta los textos a gusto: son los prompts que recibe el modelo de efectos de sonido.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bucket:
    name: str
    min_mag: float
    duration: float  # segundos (la API acepta 0.5 a 30)
    prompt: str


BUCKETS: list[Bucket] = [
    Bucket(
        "m2",
        0.0,
        0.6,
        "a single very soft dry tick, like a tiny pebble tapping stone, "
        "extremely short, close and clean, no reverb",
    ),
    Bucket(
        "m3",
        3.0,
        1.0,
        "a short muffled low thud, like a distant heavy door closing far away, "
        "subtle, clean, no music",
    ),
    Bucket(
        "m4",
        4.0,
        1.6,
        "a deep short underground rumble with a soft low impact, earth tremor, "
        "low frequencies, no music",
    ),
    Bucket(
        "m5",
        5.0,
        2.6,
        "a heavy deep seismic rumble, ground shaking, "
        "rolling low bass with gravel and rattling, no music",
    ),
    Bucket(
        "m6",
        6.0,
        4.0,
        "a massive deep earthquake rumble, huge low boom from underground "
        "followed by a long rolling tail, cinematic, no music",
    ),
    Bucket(
        "m7",
        7.0,
        6.0,
        "an enormous catastrophic earthquake, overwhelming sub-bass roar from "
        "deep in the earth, cracking rock, long ominous tail, cinematic, no music",
    ),
]
VARIATIONS = 3


def matplotlib_rgb(hex_color: str) -> tuple[float, float, float]:
    """'#rrggbb' -> (r, g, b) en 0..1, el formato que matplotlib acepta con alfa."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def bucket_for(mag: float) -> Bucket:
    chosen = BUCKETS[0]
    for b in BUCKETS:
        if mag >= b.min_mag:
            chosen = b
    return chosen


# --------------------------------------------------------------------------------------
# 1) Datos
# --------------------------------------------------------------------------------------


def cmd_fetch(args) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    end = args.end or datetime.now(UTC).strftime("%Y-%m-%d")
    params = dict(
        format="csv",
        starttime=args.start,
        endtime=end,
        minmagnitude=args.min_mag,
        orderby="time-asc",
        limit=20000,
        **BBOX,
    )
    logger.info(f"Descargando USGS {args.start} -> {end}, M>={args.min_mag} ...")
    r = requests.get(USGS_URL, params=params, timeout=120)
    r.raise_for_status()
    raw = DATA_DIR / "usgs_raw.csv"
    raw.write_text(r.text, encoding="utf-8")

    df = pd.read_csv(raw)
    df = df[["time", "latitude", "longitude", "depth", "mag", "magType", "place", "id"]]
    df = df.dropna(subset=["mag"]).sort_values("time").reset_index(drop=True)
    out = DATA_DIR / "sismos.csv"
    df.to_csv(out, index=False)
    logger.info(f"{len(df)} sismos guardados en {out.relative_to(HERE)}")
    logger.info(df["mag"].describe().round(2).to_string())


# --------------------------------------------------------------------------------------
# 2) Sonidos (ElevenLabs Sound Effects)
# --------------------------------------------------------------------------------------


def sound_path(bucket: Bucket, i: int) -> Path:
    return SOUNDS_DIR / f"{bucket.name}_{i + 1}.mp3"


def cmd_sounds(args) -> None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("Falta ELEVENLABS_API_KEY en el entorno.")
    SOUNDS_DIR.mkdir(exist_ok=True)
    manifest = {}
    for b in BUCKETS:
        for i in range(VARIATIONS):
            path = sound_path(b, i)
            manifest[path.name] = dict(
                prompt=b.prompt, duration=b.duration, min_mag=b.min_mag
            )
            if path.exists() and not args.force:
                logger.info(f"  ya existe {path.name}, lo salto")
                continue
            logger.info(f"  generando {path.name} ({b.duration}s) ...")
            r = requests.post(
                ELEVEN_URL,
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={
                    "text": b.prompt,
                    "duration_seconds": b.duration,
                    "prompt_influence": 0.6,
                },
                timeout=180,
            )
            if r.status_code != 200:
                sys.exit(f"Error {r.status_code} de ElevenLabs: {r.text[:300]}")
            path.write_bytes(r.content)
    (SOUNDS_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    logger.info(f"Banco de sonidos listo en {SOUNDS_DIR.relative_to(HERE)}/")


def load_audio(path: Path) -> np.ndarray:
    """Decodifica un archivo de audio a mono float32 a SAMPLE_RATE con ffmpeg."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


# --------------------------------------------------------------------------------------
# 3) Render
# --------------------------------------------------------------------------------------

# Rampa secuencial de un solo tono (naranja) para la profundidad:
# superficiales = claros y brillantes (los que más se sienten), profundos = oscuros.
DEPTH_RAMP = ["#7a0177", "#c51b8a", "#f768a1", "#fbb4b9", "#feebe2"]
DEPTH_MAX_KM = 150

INK = "#ffffff"
INK_MUTED = "#f3f3f3"
INK_PLACES = "#161616"
PANEL_BG = "#161616"
OCEAN = "#e5f5f9"
LAND = "#2ca25f"
COAST = "#99d8c9"

# Ajustes de legibilidad derivados de la paleta (no agregan colores nuevos):
# - Los sismos profundos (#fbb4b9, #feebe2) casi no contrastan con OCEAN (1.5:1 y
#   1.0:1) y los intermedios se pierden sobre LAND; un contorno fino de INK_PLACES
#   los separa del fondo sin alterar el color que codifica la profundidad.
# - En el panel oscuro pasa lo contrario con los superficiales (#7a0177 sobre
#   PANEL_BG = 1.8:1), así que ahí el contorno es INK_MUTED.
MARK_EDGE_MAP = (*matplotlib_rgb(INK_PLACES), 0.45)
MARK_EDGE_PANEL = (*matplotlib_rgb(INK_MUTED), 0.35)
# Opacidad de los sismos ya ocurridos. Con 0.45 el morado translúcido sobre LAND se
# volvía gris y perdía la profundidad; 0.7 conserva el tono sin tapar el mapa.
OLD_ALPHA = 0.7

# Lugares de referencia (coordenadas aproximadas). Los de LOCAL_PLACES solo se
# dibujan en regiones pequeñas, donde hay espacio.
LOCAL_PLACES = [
    ("Soná", 8.012, -81.318),
    ("Chitré", 7.961, -80.430),
    ("Las Tablas", 7.765, -80.274),
    ("Pedasí", 7.528, -80.027),
    ("Isla de Coiba", 7.450, -81.770),
]
PLACES = [
    ("Ciudad de Panamá", 8.983, -79.517),
    ("Colón", 9.359, -79.901),
    ("David", 8.427, -82.431),
    ("Santiago", 8.100, -80.983),
    ("Tonosí", 7.403, -80.441),
]
# Trazas APROXIMADAS (lat, lon), simplificadas para ilustrar; no son cartografía.
# Falla de Pedro Miguel: de Gamboa a las esclusas de Pedro Miguel.
PEDRO_MIGUEL = [(9.130, -79.705), (9.060, -79.660), (9.010, -79.620)]
# Zona de falla Azuero-Soná: cruza las penínsulas de Soná y Azuero hacia Tonosí.
AZUERO_SONA = [
    (7.85, -81.55),
    (7.68, -81.15),
    (7.55, -80.75),
    (7.42, -80.35),
    (7.35, -80.10),
]
# Límites de placa.
FRACTURA_PANAMA = [(5.0, -82.55), (6.5, -82.60), (7.4, -82.75)]  # Cocos | Nazca
FOSA_MESOAMERICANA = [(7.4, -82.75), (8.0, -83.60), (8.25, -84.0)]  # Cocos | Panamá
CINTURON_SUR = [  # Nazca | bloque de Panamá (Cinturón Deformado del Sur de Panamá)
    (7.10, -82.55),
    (6.95, -81.50),
    (6.85, -80.50),
    (6.90, -79.50),
    (7.05, -78.30),
]
PLATE_LINE = "#7d8ba0"
PLATE_LABELS = [
    # (nombre, lat, lon, alineación horizontal, región donde se dibuja)
    ("Placa de Cocos", 6.45, -83.93, "left", "panama"),
    ("Placa de Nazca", 5.60, -80.60, "center", "panama"),
    ("Placa de Nazca", 6.72, -81.40, "center", "azuero"),
    ("Placa del Caribe", 10.15, -80.70, "center", "panama"),
]


def mag_to_area(mag: np.ndarray, scale: float = 1.0) -> np.ndarray:
    # La energía crece ~10^(1.5 M); un exponente más suave deja ver a todos.
    return scale * 6.0 * (10 ** (0.45 * (mag - 2.5)))


def mag_to_gain(mag: float) -> float:
    # La magnitud ya es logarítmica: un paso lineal en dB suena natural.
    db = -30.0 + (mag - 2.5) * 6.5
    return 10 ** (min(db, -1.0) / 20)


def build_audio(
    df: pd.DataFrame, event_t: np.ndarray, total_s: float, args
) -> np.ndarray:
    rng = random.Random(42)
    bank = {}
    for b in BUCKETS:
        bank[b.name] = [
            load_audio(sound_path(b, i))
            for i in range(VARIATIONS)
            if sound_path(b, i).exists()
        ]
        if not bank[b.name]:
            sys.exit(
                f"Faltan sonidos de la categoría {b.name}. Corre primero: make sounds"
            )

    mix = np.zeros(int((total_s + 8) * SAMPLE_RATE), dtype=np.float32)
    # Polifonía limitada: en cada ventana corta suena solo el sismo más fuerte.
    window = args.sound_window
    last_slot, last_mag = None, -1.0
    order = np.argsort(event_t, kind="stable")
    chosen = []
    for idx in order:
        mag = float(df["mag"].iloc[idx])
        if mag < args.min_sound_mag:
            continue
        slot = int(event_t[idx] / window)
        if slot == last_slot:
            if mag > last_mag:
                chosen[-1] = idx
                last_mag = mag
            continue
        chosen.append(idx)
        last_slot, last_mag = slot, mag

    for idx in chosen:
        mag = float(df["mag"].iloc[idx])
        clip = rng.choice(bank[bucket_for(mag).name])
        # Pequeña variación de tono (re-muestreo ±6 %) para que no suene repetitivo.
        rate = rng.uniform(0.94, 1.06)
        n = int(len(clip) / rate)
        clip = np.interp(np.arange(n) * rate, np.arange(len(clip)), clip).astype(
            np.float32
        )
        start = int(event_t[idx] * SAMPLE_RATE)
        end = min(start + len(clip), len(mix))
        mix[start:end] += clip[: end - start] * mag_to_gain(mag)

    # Limitador suave para que las acumulaciones no saturen.
    mix = np.tanh(mix * 1.2) / np.tanh(1.2)
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = mix / peak * 0.9
    return mix[: int(total_s * SAMPLE_RATE)]


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def cmd_render(args) -> None:
    import matplotlib
    from global_land_mask import globe

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker
    from matplotlib.colors import LinearSegmentedColormap, ListedColormap, Normalize
    from pyproj import Transformer

    region = REGIONS[args.region]
    bbox = region["bbox"]

    df = pd.read_csv(DATA_DIR / "sismos.csv", parse_dates=["time"])
    df["time"] = df["time"].dt.tz_convert(TZ)  # todo se muestra en hora de Panamá
    keep = (
        (df["mag"] >= args.min_mag)
        & df["latitude"].between(bbox["minlatitude"], bbox["maxlatitude"])
        & df["longitude"].between(bbox["minlongitude"], bbox["maxlongitude"])
    )
    if args.from_date:
        keep &= df["time"] >= pd.Timestamp(args.from_date, tz=TZ)
    if args.to_date:
        keep &= df["time"] < pd.Timestamp(args.to_date, tz=TZ)
    df = df[keep].reset_index(drop=True)
    if args.limit:
        df = df.tail(args.limit).reset_index(drop=True)
    if df.empty:
        sys.exit("No hay sismos con esos filtros.")
    logger.info(f"{len(df)} sismos en la región '{args.region}'")
    OUT_DIR.mkdir(exist_ok=True)

    fps, dpi = args.fps, 100
    W, H = (1080, 1920) if args.vertical else (1920, 1080)
    if args.output == DEFAULT_OUTPUT:
        stem = "sismos_panama" if args.region == "panama" else f"sismos_{args.region}"
        args.output = f"{stem}{'_vertical' if args.vertical else ''}.mp4"

    if args.pacing == "events":
        # Un sismo cada `gap` segundos: más espacio para oír cada uno, aunque se
        # pierde la proporción del tiempo real (el contador de fecha la recupera).
        body = len(df) * args.gap
        event_t = args.intro + np.arange(len(df)) * args.gap
    else:
        # Tiempo real -> tiempo de video, lineal (refleja también que la red
        # sísmica mejoró con los años).
        body = args.duration
        elapsed = (df["time"] - df["time"].min()).dt.total_seconds().to_numpy()
        event_t = args.intro + elapsed / max(float(elapsed.max()), 1.0) * body
    total = args.intro + body + args.outro
    n_frames = int(round(total * fps))

    # Precisión de la fecha según el lapso que cubre el video.
    span_days = (df["time"].max() - df["time"].min()).days
    show_hours = span_days < 60 or args.pacing == "events"
    if show_hours:
        date_fmt = "%Y-%m-%d %H:%M"
    elif span_days < 730:
        date_fmt = "%Y-%m-%d"
    else:
        date_fmt = "%Y-%m"

    # Proyección a Web Mercator para casar con los mosaicos del mapa base.
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = tr.transform(df["longitude"].to_numpy(), df["latitude"].to_numpy())
    x0, y0 = tr.transform(bbox["minlongitude"], bbox["minlatitude"])
    x1, y1 = tr.transform(bbox["maxlongitude"], bbox["maxlatitude"])

    def inside(lat_, lon_):
        return (
            bbox["minlatitude"] <= lat_ <= bbox["maxlatitude"]
            and bbox["minlongitude"] <= lon_ <= bbox["maxlongitude"]
        )

    # Tamaño del mapa en píxeles, respetando la proporción del recuadro.
    aspect = (x1 - x0) / (y1 - y0)
    if args.vertical:
        map_w, map_h = W, int(round(W / aspect))
    else:
        map_w, map_h = int(round(H * aspect)), H

    # Mapa base sin conexión: máscara tierra/mar (~1 km) muestreada en Web Mercator.
    gx = np.linspace(x0, x1, map_w)
    gy = np.linspace(y0, y1, map_h)
    mx, my = np.meshgrid(gx, gy)
    lon, lat = tr.transform(mx, my, direction="INVERSE")
    land = globe.is_land(lat, lon).astype(float)
    # La máscara es de ~1 km: en regiones pequeñas cada celda ocupa varios píxeles y
    # la costa se ve escalonada. Un desenfoque suave antes de umbralizar la redondea.
    km_per_px = (x1 - x0) / map_w / 1000
    if km_per_px < 0.6:
        k = max(3, int(round(1.2 / km_per_px)) | 1)
        kernel = np.ones(k) / k
        for axis in (0, 1):
            land = np.apply_along_axis(np.convolve, axis, land, kernel, mode="same")
        land = (land >= 0.5).astype(float)

    fig = plt.figure(figsize=(W / dpi, H / dpi), dpi=dpi, facecolor=PANEL_BG)
    if args.vertical:  # mapa arriba, a todo el ancho
        map_top = 0.875
        ax = fig.add_axes([0, map_top - map_h / H, 1, map_h / H])
    else:  # mapa a la izquierda, a toda la altura
        ax = fig.add_axes([0, 0, map_w / W, 1])
    ax.set_facecolor(OCEAN)
    ax.imshow(
        land,
        extent=(x0, x1, y0, y1),
        origin="lower",
        interpolation="nearest",
        cmap=ListedColormap([OCEAN, LAND]),
        vmin=0,
        vmax=1,
    )
    ax.contour(gx, gy, land, levels=[0.5], colors=[COAST], linewidths=0.8)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_axis_off()

    # Referencias geográficas (solo las que caen dentro del recuadro).
    places = PLACES + (LOCAL_PLACES if args.region != "panama" else [])
    places = [p for p in places if inside(p[1], p[2])]
    px, py = tr.transform([p[2] for p in places], [p[1] for p in places])
    ax.scatter(px, py, s=10, c=INK_PLACES, zorder=3, linewidths=0)
    for (name, _, _), xx, yy in zip(places, px, py, strict=True):
        ax.annotate(
            name,
            (xx, yy),
            xytext=(6, 4),
            textcoords="offset points",
            color=INK_PLACES,
            fontsize=11,
            zorder=3,
        )

    from matplotlib import patheffects

    def proj(line):
        return tr.transform([p[1] for p in line], [p[0] for p in line])

    # Las líneas y etiquetas van encima de los sismos, con un halo oscuro para que
    # se lean aunque haya un enjambre debajo.
    halo = [patheffects.withStroke(linewidth=4.5, foreground=OCEAN, alpha=0.7)]
    label_box = dict(boxstyle="round,pad=0.25", fc=OCEAN, ec="none", alpha=0.9)

    # Límites de placa (debajo de los sismos) y nombres de las placas.
    for line in (FRACTURA_PANAMA, FOSA_MESOAMERICANA, CINTURON_SUR):
        ax.plot(
            *proj(line),
            color=PLATE_LINE,
            lw=1.8,
            zorder=6,
            solid_capstyle="round",
            path_effects=halo,
        )
    for name, lat_, lon_, ha, where in PLATE_LABELS:
        if where != args.region:
            continue
        ax.text(
            *tr.transform(lon_, lat_),
            name.upper(),
            color=PLATE_LINE,
            fontsize=13,
            weight="bold",
            ha=ha,
            va="center",
            zorder=2,
        )
    # Nombres de los límites: (texto, lat, lon, rotación, alineación, región).
    for text, lat_, lon_, rot, ha, where in (
        ("Zona de Fractura de Panamá", 5.25, -82.85, 90, "left", "panama"),
        ("Cinturón Deformado del Sur", 6.78, -79.40, 3, "left", "panama"),
        ("Cinturón Deformado del Sur", 6.95, -80.45, 3, "left", "azuero"),
        ("Fosa Mesoamericana", 7.72, -83.55, -33, "center", "panama"),
    ):
        if where != args.region:
            continue
        ax.text(
            *tr.transform(lon_, lat_),
            text,
            rotation=rot,
            rotation_mode="anchor",
            ha=ha,
            va="center",
            color=PLATE_LINE,
            fontsize=10,
            style="italic",
            zorder=7,
            bbox=label_box,
        )

    # Fallas.
    ax.plot(
        *proj(AZUERO_SONA),
        color=PLATE_LINE,
        lw=2,
        ls=(0, (4, 2)),
        zorder=6,
        path_effects=halo,
    )
    ax_x, ax_y = proj(AZUERO_SONA)
    ax.annotate(
        "Zona de falla Azuero–Soná",
        (ax_x[1], ax_y[1]),
        xytext=(-30, -60) if args.region == "panama" else (10, -110),
        textcoords="offset points",
        color=PLATE_LINE,
        fontsize=12,
        ha="right" if args.region == "panama" else "left",
        zorder=7,
        bbox=label_box,
        arrowprops=dict(arrowstyle="-", color=PLATE_LINE, lw=0.8, shrinkA=2, shrinkB=2),
    )
    fx, fy = proj(PEDRO_MIGUEL)
    ax.plot(fx, fy, color=PLATE_LINE, lw=2, ls=(0, (4, 2)), zorder=6, path_effects=halo)
    ax.annotate(
        "Falla de Pedro Miguel",
        (fx[-1], fy[-1]),
        xytext=(-40, -38),
        textcoords="offset points",
        color=PLATE_LINE,
        fontsize=12,
        ha="right",
        zorder=7,
        bbox=label_box,
        arrowprops=dict(arrowstyle="-", color=PLATE_LINE, lw=0.8, shrinkA=2, shrinkB=2),
    )

    from matplotlib.lines import Line2D

    leg = ax.legend(
        handles=[
            Line2D([], [], color=PLATE_LINE, lw=1.8, label="Límite de placas"),
            Line2D([], [], color=PLATE_LINE, lw=2, ls=(0, (4, 2)), label="Falla"),
        ],
        loc="upper right",
        frameon=True,
        facecolor=OCEAN,
        edgecolor="none",
        framealpha=0.8,
        fontsize=11,
        labelcolor=INK_PLACES,
        handlelength=2.6,
    )
    leg.set_zorder(8)

    cmap = LinearSegmentedColormap.from_list("depth", DEPTH_RAMP)
    norm = Normalize(0, DEPTH_MAX_KM)
    base_rgba = cmap(norm(np.clip(df["depth"].to_numpy(), 0, DEPTH_MAX_KM)))
    # En regiones pequeñas los círculos crecen un poco para no perderse en el zoom.
    zoom = (BBOX["maxlongitude"] - BBOX["minlongitude"]) / (
        bbox["maxlongitude"] - bbox["minlongitude"]
    )
    size_scale = zoom**0.7
    areas = mag_to_area(df["mag"].to_numpy(), size_scale)

    # Dos capas: puntos ya ocurridos (persisten, atenuados) y el "destello" reciente.
    old = ax.scatter([], [], s=[], zorder=4, linewidths=0.5, edgecolors=[MARK_EDGE_MAP])
    # El anillo lleva debajo una copia más gruesa en INK_PLACES, que se desvanece con
    # él: así se ve también cuando el color de profundidad es claro.
    flash_bg = ax.scatter([], [], s=[], zorder=5, facecolors="none", linewidths=3.5)
    flash = ax.scatter([], [], s=[], zorder=5, facecolors="none", linewidths=2)

    # ---- panel de información ----
    # Posiciones (fracciones de la figura) para cada orientación.
    if args.vertical:
        left, right = 0.06, 0.94
        L = dict(
            title=(left, 0.945, 40),
            subtitle=(left, 0.918, 17),
            date=(left, 0.392, 52),
            count=(left, 0.366, 20),
            max=(left, 0.322, 17),
            mag_label=(left, 0.285),
            mag_axes=[left, 0.205, 0.42, 0.07],
            depth_label=(0.56, 0.285),
            depth_axes=[0.56, 0.255, right - 0.56, 0.012],
            timeline=[left + 0.07, 0.06, right - left - 0.07, 0.115],
            footer=(left, 0.012),
        )
    else:
        left, right = ax.get_position().x1 + 0.025, 0.975
        pw = right - left
        L = dict(
            title=(left, 0.90, 30),
            subtitle=(left, 0.855, 14),
            date=(left, 0.74, 40),
            count=(left, 0.69, 17),
            max=(left, 0.64, 15),
            mag_label=(left, 0.555),
            mag_axes=[left, 0.43, pw, 0.11],
            depth_label=(left, 0.395),
            depth_axes=[left, 0.36, pw * 0.92, 0.018],
            timeline=[left + 0.03, 0.1, pw - 0.035, 0.19],
            footer=(left, 0.03),
        )

    def put(key, text="", **kw):
        xx, yy, size = L[key]
        return fig.text(xx, yy, text, fontsize=size, **kw)

    title = put("title", region["title"], color=INK, weight="bold")

    def fit(txt, sample=None, min_size=12):
        """Achica un texto hasta que quepa entre su x y el borde derecho del panel."""
        max_w = (right - txt.get_position()[0]) * W
        final = txt.get_text()
        if sample is not None:
            txt.set_text(sample)
        renderer = fig.canvas.get_renderer()
        while (
            txt.get_window_extent(renderer).width > max_w
            and txt.get_fontsize() > min_size
        ):
            txt.set_fontsize(txt.get_fontsize() - 1)
        txt.set_text(final)

    fit(title)
    subtitle = put(
        "subtitle",
        f"Catálogo USGS · M ≥ {args.min_mag} · "
        + (
            f"{df['time'].min().year}"
            if df["time"].min().year == df["time"].max().year
            else f"{df['time'].min().year}–{df['time'].max().year}"
        )
        + (" · hora de Panamá" if show_hours else ""),
        color=INK_MUTED,
    )
    fit(subtitle, min_size=9)
    date_txt = put("date", color=INK, weight="bold", family="monospace")
    fit(date_txt, sample=df["time"].max().strftime(date_fmt))
    count_txt = put("count", color=INK_MUTED)
    max_txt = put("max", color=INK_MUTED)

    # Leyenda de tamaño (magnitud).
    fig.text(*L["mag_label"], "Magnitud", color=INK, fontsize=15, weight="bold")
    lax = fig.add_axes(L["mag_axes"], facecolor=PANEL_BG)
    lax.set_axis_off()
    lax.set_xlim(-0.3, 5.0)
    lax.set_ylim(-1, 1)
    for k, m in enumerate([3, 4, 5, 6, 7]):
        lax.scatter(
            [k * 1.1 + 0.2],
            [0.15],
            s=mag_to_area(np.array([m]), size_scale)[0],
            c="none",
            edgecolors=INK,
            linewidths=1.2,
            clip_on=False,
        )
        lax.text(
            k * 1.1 + 0.2, -0.85, str(m), color=INK_MUTED, fontsize=12, ha="center"
        )

    # Barra de color (profundidad).
    fig.text(
        *L["depth_label"], "Profundidad (km)", color=INK, fontsize=15, weight="bold"
    )
    cax = fig.add_axes(L["depth_axes"])
    cb = fig.colorbar(
        plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal"
    )
    # El extremo superficial (#7a0177) se funde con PANEL_BG: un borde fino lo delimita.
    cb.outline.set_edgecolor(MARK_EDGE_PANEL)
    cb.outline.set_linewidth(0.8)
    cb.set_ticks([0, 50, 100, 150])
    cb.set_ticklabels(["0", "50", "100", "150+"])
    cax.tick_params(colors=INK_MUTED, labelsize=12, length=0)

    # Línea de tiempo: magnitud vs fecha, se va revelando.
    tax = fig.add_axes(L["timeline"], facecolor=PANEL_BG)
    tax.scatter(df["time"], df["mag"], s=4, c=INK_MUTED, alpha=0.18, linewidths=0)
    tl = tax.scatter(
        [], [], s=7, linewidths=0.4, edgecolors=[MARK_EDGE_PANEL], zorder=3
    )
    cursor = tax.axvline(df["time"].min(), color=INK, lw=1)
    tax.set_ylim(args.min_mag - 0.2, max(7.6, df["mag"].max() + 0.3))
    tax.set_xlim(df["time"].min(), df["time"].max())
    for s in ("top", "right"):
        tax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        tax.spines[s].set_color("#3a3a37")
    tax.tick_params(colors=INK_MUTED, labelsize=11)
    loc = matplotlib.dates.AutoDateLocator(minticks=3, maxticks=5)
    tax.xaxis.set_major_locator(loc)
    if span_days < 730:  # fechas en español: "9 oct"

        def fmt_fecha(v, _pos):
            d = matplotlib.dates.num2date(v, tz=TZ)
            return f"{d.day} {MESES[d.month - 1]}"

        tax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(fmt_fecha))
    else:
        step = max(1, int(span_days / 365.25 / 5))
        tax.xaxis.set_major_locator(matplotlib.dates.YearLocator(step))
        tax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y"))
    tax.set_ylabel("Magnitud", color=INK_MUTED, fontsize=12)
    tax.grid(axis="y", color="#2a2a28", lw=0.8)

    fig.text(
        *L["footer"],
        "Datos: USGS ComCat · Costas: GLOBE (global-land-mask)\n"
        "Sonido: ElevenLabs · Fallas y placas: trazas aproximadas",
        color="#6e6d69",
        fontsize=10,
        linespacing=1.5,
    )

    times = df["time"]
    mags = df["mag"].to_numpy()
    fade_s = args.flash

    # Temporales con el nombre de la salida: permite varios renders a la vez.
    stem = Path(args.output).stem
    video_tmp = OUT_DIR / f"_{stem}_video.mp4"
    ff = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s",
            f"{W}x{H}",
            "-r",
            str(fps),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "20",
            "-preset",
            "medium",
            str(video_tmp),
        ],
        stdin=subprocess.PIPE,
    )

    logger.info(f"Dibujando {n_frames} cuadros ({total:.0f} s a {fps} fps) ...")
    for f in range(n_frames):
        t = f / fps
        visible = event_t <= t
        n = int(visible.sum())

        if n:
            age = t - event_t[visible]
            rgba = base_rgba[visible].copy()
            rgba[:, 3] = np.where(age < fade_s, 0.95, OLD_ALPHA)
            old.set_offsets(np.column_stack([x[visible], y[visible]]))
            old.set_sizes(areas[visible])
            old.set_facecolors(rgba)

            recent = visible & (t - event_t < fade_s)
            if recent.any():
                a = (t - event_t[recent]) / fade_s  # 0 -> 1
                ring = base_rgba[recent].copy()
                ring[:, 3] = 1 - a
                pts = np.column_stack([x[recent], y[recent]])
                sizes = areas[recent] * (1 + 5 * a) ** 2
                under = np.tile([*matplotlib_rgb(INK_PLACES), 0.0], (len(a), 1))
                under[:, 3] = 0.5 * (1 - a)
                for layer, colors in ((flash_bg, under), (flash, ring)):
                    layer.set_offsets(pts)
                    layer.set_sizes(sizes)
                    layer.set_edgecolors(colors)
            else:
                flash.set_offsets(np.empty((0, 2)))
                flash_bg.set_offsets(np.empty((0, 2)))

            current = times[visible].iloc[-1]
            date_txt.set_text(current.strftime(date_fmt))
            count_txt.set_text(f"{n:,} sismos".replace(",", "."))
            imax = int(np.argmax(np.where(visible, mags, -1)))
            max_txt.set_text(
                f"Mayor hasta ahora: M{mags[imax]:.1f} · "
                f"{times.iloc[imax]:%Y-%m-%d}\n{str(df['place'].iloc[imax])[:40]}"
            )
            tl.set_offsets(
                np.column_stack(
                    [matplotlib.dates.date2num(times[visible]), mags[visible]]
                )
            )
            tl.set_facecolors(base_rgba[visible])
            cursor.set_xdata([current, current])
        else:
            date_txt.set_text(times.iloc[0].strftime(date_fmt))
            count_txt.set_text("0 sismos")

        fig.canvas.draw()
        ff.stdin.write(fig.canvas.buffer_rgba().tobytes())
        if f % (fps * 5) == 0:
            logger.info(f"  {t:5.1f} s · {n} sismos")

    ff.stdin.close()
    ff.wait()
    plt.close(fig)

    logger.info("Mezclando audio ...")
    audio = build_audio(df, event_t, total, args)
    wav = OUT_DIR / f"_{stem}_audio.wav"
    write_wav(wav, audio)

    out = OUT_DIR / args.output
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video_tmp),
            "-i",
            str(wav),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out),
        ],
        check=True,
    )
    video_tmp.unlink()
    wav.unlink()
    logger.info(f"Listo: {out.relative_to(HERE)}")


# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_fetch(sp):
        sp.add_argument("--start", default="1970-01-01")
        sp.add_argument("--end", default=None, help="por defecto, hoy")
        sp.add_argument("--min-mag", type=float, default=2.5, dest="min_mag")

    def add_sounds(sp):
        sp.add_argument(
            "--force", action="store_true", help="regenera aunque ya existan"
        )

    def add_render(sp):
        sp.add_argument(
            "--duration", type=float, default=60, help="segundos de animación"
        )
        sp.add_argument("--intro", type=float, default=2)
        sp.add_argument("--outro", type=float, default=4)
        sp.add_argument("--fps", type=int, default=30)
        sp.add_argument(
            "--flash", type=float, default=0.8, help="duración del destello (s)"
        )
        sp.add_argument(
            "--min-sound-mag",
            type=float,
            default=3.0,
            dest="min_sound_mag",
            help="los sismos por debajo de esta magnitud se ven pero no suenan",
        )
        sp.add_argument(
            "--sound-window",
            type=float,
            default=0.12,
            dest="sound_window",
            help="en cada ventana de este largo (s) suena solo el sismo más fuerte",
        )
        sp.add_argument(
            "--limit", type=int, default=0, help="solo los últimos N sismos (pruebas)"
        )
        sp.add_argument("--output", default=DEFAULT_OUTPUT)
        sp.add_argument(
            "--region",
            choices=sorted(REGIONS),
            default="panama",
            help="recorte del mapa (el catálogo siempre cubre todo Panamá)",
        )
        sp.add_argument(
            "--from", dest="from_date", default=None, help="solo desde esta fecha"
        )
        sp.add_argument(
            "--to", dest="to_date", default=None, help="solo hasta esta fecha (excl.)"
        )
        sp.add_argument(
            "--pacing",
            choices=["time", "events"],
            default="time",
            help="time: proporcional al tiempo real · events: un sismo cada --gap s",
        )
        sp.add_argument(
            "--gap",
            type=float,
            default=0.5,
            help="segundos entre sismos con --pacing events",
        )
        sp.add_argument(
            "--vertical",
            action="store_true",
            help="formato vertical 1080x1920 (reels, shorts, stories)",
        )

    add_fetch(sub.add_parser("fetch", help="descarga datos del USGS"))
    add_sounds(sub.add_parser("sounds", help="genera el banco de sonidos"))
    r = sub.add_parser("render", help="renderiza el video")
    add_render(r)
    r.add_argument("--min-mag", type=float, default=2.5, dest="min_mag")
    a = sub.add_parser("all", help="fetch + sounds + render")
    add_fetch(a)
    add_sounds(a)
    add_render(a)

    args = p.parse_args(argv)
    if args.cmd in ("fetch", "all"):
        cmd_fetch(args)
    if args.cmd in ("sounds", "all"):
        cmd_sounds(args)
    if args.cmd in ("render", "all"):
        cmd_render(args)
