#!/usr/bin/env python3
"""
Sismos de Panamá: mapa animado + sonificación por magnitud.

Pasos (cada uno guarda su resultado y se puede correr por separado):

    python sismos.py fetch     # descarga el catálogo del USGS -> data/sismos.csv
    python sismos.py sounds    # genera el banco de sonidos con ElevenLabs -> sounds/
    python sismos.py render    # dibuja el mapa, mezcla el audio y exporta out/sismos_panama.mp4
    python sismos.py all       # los tres en orden

La clave de ElevenLabs se lee de la variable ELEVENLABS_API_KEY (solo hace falta en `sounds`).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
SOUNDS_DIR = HERE / "sounds"
OUT_DIR = HERE / "out"

# Recuadro de Panamá (incluye la zona de fractura de Panamá y el sur de Azuero).
BBOX = dict(minlatitude=5.0, maxlatitude=10.5, minlongitude=-84.0, maxlongitude=-76.5)

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
ELEVEN_URL = "https://api.elevenlabs.io/v1/sound-generation"

SAMPLE_RATE = 44100
# --------------------------------------------------------------------------------------
# Banco de sonidos: una categoría por rango de magnitud, varias variaciones por categoría.
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
        "a single very soft dry tick, like a tiny pebble tapping stone, extremely short, close and clean, no reverb",
    ),
    Bucket(
        "m3",
        3.0,
        1.0,
        "a short muffled low thud, like a distant heavy door closing far away, subtle, clean, no music",
    ),
    Bucket(
        "m4",
        4.0,
        1.6,
        "a deep short underground rumble with a soft low impact, earth tremor, low frequencies, no music",
    ),
    Bucket(
        "m5",
        5.0,
        2.6,
        "a heavy deep seismic rumble, ground shaking, rolling low bass with gravel and rattling, no music",
    ),
    Bucket(
        "m6",
        6.0,
        4.0,
        "a massive deep earthquake rumble, huge low boom from underground followed by a long rolling tail, cinematic, no music",
    ),
    Bucket(
        "m7",
        7.0,
        6.0,
        "an enormous catastrophic earthquake, overwhelming sub-bass roar from deep in the earth, cracking rock, long ominous tail, cinematic, no music",
    ),
]
VARIATIONS = 3


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
    end = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params = dict(
        format="csv",
        starttime=args.start,
        endtime=end,
        minmagnitude=args.min_mag,
        orderby="time-asc",
        limit=20000,
        **BBOX,
    )
    print(f"Descargando USGS {args.start} -> {end}, M>={args.min_mag} ...")
    r = requests.get(USGS_URL, params=params, timeout=120)
    r.raise_for_status()
    raw = DATA_DIR / "usgs_raw.csv"
    raw.write_text(r.text, encoding="utf-8")

    df = pd.read_csv(raw)
    df = df[["time", "latitude", "longitude", "depth", "mag", "magType", "place", "id"]]
    df = df.dropna(subset=["mag"]).sort_values("time").reset_index(drop=True)
    out = DATA_DIR / "sismos.csv"
    df.to_csv(out, index=False)
    print(f"{len(df)} sismos guardados en {out.relative_to(HERE)}")
    print(df["mag"].describe().round(2).to_string())


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
                print(f"  ya existe {path.name}, lo salto")
                continue
            print(f"  generando {path.name} ({b.duration}s) ...")
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
    print(f"Banco de sonidos listo en {SOUNDS_DIR.relative_to(HERE)}/")


def load_audio(path: Path) -> np.ndarray:
    """Decodifica cualquier archivo de audio a mono float32 a SAMPLE_RATE usando ffmpeg."""
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
DEPTH_RAMP = ["#ffd9c2", "#f6a77c", "#eb6834", "#c24a1c", "#8a3212"]
DEPTH_MAX_KM = 150

INK = "#f2f1ee"
INK_MUTED = "#a3a29d"
PANEL_BG = "#161616"

# Lugares de referencia (coordenadas aproximadas).
PLACES = [
    ("Ciudad de Panamá", 8.983, -79.517),
    ("Colón", 9.359, -79.901),
    ("David", 8.427, -82.431),
    ("Santiago", 8.100, -80.983),
    ("Tonosí", 7.403, -80.441),
]
# Traza aproximada de la falla de Pedro Miguel (Gamboa -> esclusas de Pedro Miguel).
PEDRO_MIGUEL = [(9.130, -79.705), (9.060, -79.660), (9.010, -79.620)]


def mag_to_area(mag: np.ndarray, scale: float = 1.0) -> np.ndarray:
    # La energía crece ~10^(1.5 M); usamos un exponente más suave para que se lean todos.
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
                f"Faltan sonidos de la categoría {b.name}. Corre primero: python sismos.py sounds"
            )

    mix = np.zeros(int((total_s + 8) * SAMPLE_RATE), dtype=np.float32)
    # Limitamos la polifonía: dentro de cada ventana corta suena solo el sismo más fuerte.
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
    import contextily as cx
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from pyproj import Transformer

    df = pd.read_csv(DATA_DIR / "sismos.csv", parse_dates=["time"])
    df = df[df["mag"] >= args.min_mag].reset_index(drop=True)
    if args.limit:
        df = df.tail(args.limit).reset_index(drop=True)
    OUT_DIR.mkdir(exist_ok=True)

    fps, W, H, dpi = args.fps, 1920, 1080, 100
    intro, outro, body = args.intro, args.outro, args.duration
    total = intro + body + outro
    n_frames = int(round(total * fps))

    # Tiempo real -> tiempo de video (lineal: refleja también que la red sísmica mejoró con los años).
    elapsed = (df["time"] - df["time"].min()).dt.total_seconds().to_numpy()
    frac = elapsed / max(float(elapsed.max()), 1.0)
    event_t = intro + frac * body

    # Proyección a Web Mercator para casar con los mosaicos del mapa base.
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = tr.transform(df["longitude"].to_numpy(), df["latitude"].to_numpy())
    x0, y0 = tr.transform(BBOX["minlongitude"], BBOX["minlatitude"])
    x1, y1 = tr.transform(BBOX["maxlongitude"], BBOX["maxlatitude"])

    # Mapa base sin conexión: máscara tierra/mar (~1 km) muestreada en Web Mercator.
    map_w = int(round(H * (x1 - x0) / (y1 - y0)))
    gx = np.linspace(x0, x1, map_w)
    gy = np.linspace(y0, y1, H)
    mx, my = np.meshgrid(gx, gy)
    lon, lat = tr.transform(mx, my, direction="INVERSE")
    land = globe.is_land(lat, lon).astype(float)

    fig = plt.figure(figsize=(W / dpi, H / dpi), dpi=dpi, facecolor=PANEL_BG)
    ax = fig.add_axes([0, 0, map_w / W, 1])  # mapa a la izquierda, a escala
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

    # Referencias geográficas.
    px, py = tr.transform([p[2] for p in PLACES], [p[1] for p in PLACES])
    ax.scatter(px, py, s=10, c=INK_MUTED, zorder=3, linewidths=0)
    for (name, _, _), xx, yy in zip(PLACES, px, py):
        ax.annotate(
            name,
            (xx, yy),
            xytext=(6, 4),
            textcoords="offset points",
            color=INK_MUTED,
            fontsize=11,
            zorder=3,
        )
    fx, fy = tr.transform([p[1] for p in PEDRO_MIGUEL], [p[0] for p in PEDRO_MIGUEL])
    ax.plot(fx, fy, color=INK, lw=2, ls=(0, (4, 2)), zorder=3, solid_capstyle="round")
    ax.annotate(
        "Falla de\nPedro Miguel",
        (fx[0], fy[0]),
        xytext=(-14, 14),
        textcoords="offset points",
        color=INK,
        fontsize=12,
        ha="right",
        zorder=3,
    )

    cmap = LinearSegmentedColormap.from_list("depth", DEPTH_RAMP)
    norm = Normalize(0, DEPTH_MAX_KM)
    base_rgba = cmap(norm(np.clip(df["depth"].to_numpy(), 0, DEPTH_MAX_KM)))
    areas = mag_to_area(df["mag"].to_numpy())

    # Dos capas: puntos ya ocurridos (persisten, atenuados) y el "destello" del sismo reciente.
    old = ax.scatter([], [], s=[], zorder=4, linewidths=0)
    flash = ax.scatter([], [], s=[], zorder=5, facecolors="none", linewidths=2)

    # ---- panel derecho ----
    left = ax.get_position().x1 + 0.035
    fig.text(left, 0.90, "Sismos en Panamá", color=INK, fontsize=34, weight="bold")
    fig.text(
        left,
        0.855,
        f"Catálogo USGS · M ≥ {args.min_mag} · "
        f"{df['time'].min().year}–{df['time'].max().year}",
        color=INK_MUTED,
        fontsize=15,
    )
    date_txt = fig.text(
        left, 0.74, "", color=INK, fontsize=44, weight="bold", family="monospace"
    )
    count_txt = fig.text(left, 0.69, "", color=INK_MUTED, fontsize=17)
    max_txt = fig.text(left, 0.64, "", color=INK_MUTED, fontsize=15, wrap=True)

    # Leyenda de tamaño (magnitud).
    fig.text(left, 0.555, "Magnitud", color=INK, fontsize=15, weight="bold")
    lax = fig.add_axes([left, 0.44, 0.18, 0.1], facecolor=PANEL_BG)
    lax.set_axis_off()
    lax.set_xlim(0, 4.6)
    lax.set_ylim(-1, 1)
    for k, m in enumerate([3, 4, 5, 6, 7]):
        lax.scatter(
            [k * 1.1 + 0.2],
            [0.15],
            s=mag_to_area(np.array([m]))[0] * 0.5,
            c="none",
            edgecolors=INK,
            linewidths=1.2,
        )
        lax.text(
            k * 1.1 + 0.2, -0.85, str(m), color=INK_MUTED, fontsize=12, ha="center"
        )

    # Barra de color (profundidad).
    fig.text(left, 0.395, "Profundidad (km)", color=INK, fontsize=15, weight="bold")
    cax = fig.add_axes([left, 0.365, 0.2, 0.018])
    cb = fig.colorbar(
        plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal"
    )
    cb.outline.set_visible(False)
    cb.set_ticks([0, 50, 100, 150])
    cb.set_ticklabels(["0", "50", "100", "150+"])
    cax.tick_params(colors=INK_MUTED, labelsize=12, length=0)

    # Línea de tiempo: magnitud vs fecha, se va revelando.
    tax = fig.add_axes([left, 0.08, 0.27, 0.22], facecolor=PANEL_BG)
    tax.scatter(df["time"], df["mag"], s=4, c=INK_MUTED, alpha=0.18, linewidths=0)
    tl = tax.scatter([], [], s=6, linewidths=0, zorder=3)
    cursor = tax.axvline(df["time"].min(), color=INK, lw=1)
    tax.set_ylim(args.min_mag - 0.2, max(7.6, df["mag"].max() + 0.3))
    tax.set_xlim(df["time"].min(), df["time"].max())
    for s in ("top", "right"):
        tax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        tax.spines[s].set_color("#3a3a37")
    tax.tick_params(colors=INK_MUTED, labelsize=11)
    tax.set_ylabel("Magnitud", color=INK_MUTED, fontsize=12)
    tax.grid(axis="y", color="#2a2a28", lw=0.8)

    fig.text(
        left,
        0.025,
        "Datos: USGS ComCat · Mapa: © OpenStreetMap, © CARTO · Sonido: ElevenLabs",
        color="#6e6d69",
        fontsize=10,
    )

    times = df["time"]
    mags = df["mag"].to_numpy()
    fade_s = args.flash
    video_tmp = OUT_DIR / "_video.mp4"
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

    print(f"Dibujando {n_frames} cuadros ({total:.0f} s a {fps} fps) ...")
    for f in range(n_frames):
        t = f / fps
        visible = event_t <= t
        n = int(visible.sum())

        if n:
            age = t - event_t[visible]
            rgba = base_rgba[visible].copy()
            rgba[:, 3] = np.where(age < fade_s, 0.95, 0.45)
            old.set_offsets(np.column_stack([x[visible], y[visible]]))
            old.set_sizes(areas[visible])
            old.set_facecolors(rgba)

            recent = visible & (t - event_t < fade_s)
            if recent.any():
                a = (t - event_t[recent]) / fade_s  # 0 -> 1
                ring = base_rgba[recent].copy()
                ring[:, 3] = 1 - a
                flash.set_offsets(np.column_stack([x[recent], y[recent]]))
                flash.set_sizes(areas[recent] * (1 + 5 * a) ** 2)
                flash.set_edgecolors(ring)
            else:
                flash.set_offsets(np.empty((0, 2)))

            current = times[visible].iloc[-1]
            date_txt.set_text(current.strftime("%Y-%m"))
            count_txt.set_text(f"{n:,} sismos".replace(",", "."))
            imax = int(np.argmax(np.where(visible, mags, -1)))
            max_txt.set_text(
                f"Mayor hasta ahora: M{mags[imax]:.1f} · "
                f"{times.iloc[imax]:%Y-%m-%d}\n{str(df['place'].iloc[imax])[:48]}"
            )
            tl.set_offsets(
                np.column_stack(
                    [matplotlib.dates.date2num(times[visible]), mags[visible]]
                )
            )
            tl.set_facecolors(base_rgba[visible])
            cursor.set_xdata([current, current])
        else:
            date_txt.set_text(f"{times.iloc[0]:%Y-%m}")
            count_txt.set_text("0 sismos")

        fig.canvas.draw()
        ff.stdin.write(fig.canvas.buffer_rgba().tobytes())
        if f % (fps * 5) == 0:
            print(f"  {t:5.1f} s · {n} sismos")

        fig.canvas.draw()
        ff.stdin.write(fig.canvas.buffer_rgba().tobytes())
        if f % (fps * 5) == 0:
            print(f"  {t:5.1f} s · {n} sismos")

    ff.stdin.close()
    ff.wait()
    plt.close(fig)

    print("Mezclando audio ...")
    audio = build_audio(df, event_t, total, args)
    wav = OUT_DIR / "_audio.wav"
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
    print(f"Listo: {out.relative_to(HERE)}")


# --------------------------------------------------------------------------------------


def main() -> None:
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
            "--zoom", type=int, default=8, help="zoom de los mosaicos del mapa base"
        )
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
        sp.add_argument("--output", default="sismos_panama.mp4")

    add_fetch(sub.add_parser("fetch", help="descarga datos del USGS"))
    add_sounds(sub.add_parser("sounds", help="genera el banco de sonidos"))
    r = sub.add_parser("render", help="renderiza el video")
    add_render(r)
    r.add_argument("--min-mag", type=float, default=2.5, dest="min_mag")
    a = sub.add_parser("all", help="fetch + sounds + render")
    add_fetch(a)
    add_sounds(a)
    add_render(a)

    args = p.parse_args()
    if args.cmd in ("fetch", "all"):
        cmd_fetch(args)
    if args.cmd in ("sounds", "all"):
        cmd_sounds(args)
    if args.cmd in ("render", "all"):
        cmd_render(args)


if __name__ == "__main__":
    main()
