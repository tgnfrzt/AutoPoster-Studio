import math
from typing import Tuple, List
try:
    from core.config import settings
except ImportError:
    from backend.core.config import settings

def get_antifingerprint_video_filter(
    input_tag: str = "0:v",
    output_tag: str = "v_shield",
    target_width: int = 1080,
    target_height: int = 1920,
    tilt_deg: float = 1.8,
    zoom: float = 1.07,
    speed: float = 1.03
) -> str:
    """
    Builds an extreme anti-fingerprint video filter chain that obliterates Meta/Instagram/TikTok
    perceptual hashing (pHash, DCT, block-mean, and neural embedding matches):
    1. Spatial grid destruction: Micro-rotation / tilt (1.8 degrees).
    2. Dynamic crop & zoom: 1.07x scale to completely eliminate rotated edges and border markers.
    3. Cinematic grading: Custom contrast, saturation, gamma, and RGB color balance curves.
    4. Film grain dither: Temporal uniform dither (noise=alls=6) breaks block DCT matching.
    5. Cinematic vignette: Alters global luminosity matrix.
    6. Temporal desynchronization: 1.03x speed stretch via setpts shifts all keyframe timestamps.
    """
    rad = tilt_deg * math.pi / 180.0
    rad_str = f"{rad:.5f}"

    vf = (
        f"[{input_tag}]scale={target_width}:{target_height},"
        f"rotate={rad_str}:ow=rotw({rad_str}):oh=roth({rad_str}):c=black,"
        f"scale=iw*{zoom:.2f}:ih*{zoom:.2f},crop={target_width}:{target_height},"
        f"eq=contrast=1.07:brightness=0.012:saturation=1.08:gamma=1.03,"
        f"colorbalance=rs=0.03:gs=-0.01:bs=0.02:rm=0.02:gm=-0.01:bm=0.03,"
        f"vignette=PI/5,"
        f"noise=alls=6:allf=t+u,"
        f"setpts=PTS/{speed:.3f},"
        f"setsar=1,fps=30,format=yuv420p[{output_tag}]"
    )
    return vf

def get_antifingerprint_audio_filter(
    input_tag: str = "0:a",
    output_tag: str = "a_shield",
    speed: float = 1.03,
    pitch_factor: float = 1.012
) -> str:
    """
    Builds an acoustic anti-fingerprint filter chain that shatters Shazam / Meta audio fingerprinting:
    1. Micro-pitch shift: 1.2% frequency shift via asetrate/aresample.
    2. Tempo compensation: atempo matching video speed (speed / pitch_factor) for 100% sync.
    3. Harmonic coloration: Dual-band parametric EQ modifying acoustic spectrum peaks.
    """
    tempo_comp = speed / pitch_factor
    af = (
        f"[{input_tag}]asetrate=44100*{pitch_factor:.4f},aresample=44100,"
        f"atempo={tempo_comp:.4f},"
        f"equalizer=f=800:width_type=q:width=2:g=1.2,"
        f"equalizer=f=3200:width_type=q:width=1.5:g=-1.0[{output_tag}]"
    )
    return af

def get_antifingerprint_ffmpeg_args() -> List[str]:
    """Returns metadata-stripping and bitexact flags to eliminate device and encoder signatures."""
    return [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-fflags", "+bitexact",
        "-flags", "+bitexact"
    ]
