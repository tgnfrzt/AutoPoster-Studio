import os
import re
from typing import List, Dict, Any, Optional

def format_ass_time(seconds: float) -> str:
    """Formats seconds into ASS timestamp: H:MM:SS.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def generate_word_timestamps_whisper(audio_path: str) -> List[Dict[str, Any]]:
    """Transcribes audio using faster-whisper with exact word timestamps."""
    from faster_whisper import WhisperModel
    # Use base.en for high speed and accuracy on CPU/GPU
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, word_timestamps=True, language="en")
    words = []
    for segment in segments:
        for w in segment.words:
            clean_word = w.word.strip()
            if clean_word:
                words.append({
                    "word": clean_word,
                    "start": max(0.0, float(w.start)),
                    "end": max(float(w.start) + 0.05, float(w.end))
                })
    return words

def generate_fallback_word_timestamps(text: str, total_duration: float) -> List[Dict[str, Any]]:
    """Proportional timing fallback when Whisper is initializing or offline."""
    raw_words = text.split()
    if not raw_words:
        return []
    total_chars = sum(len(w) for w in raw_words)
    current_time = 0.1
    words = []
    for w in raw_words:
        fraction = len(w) / max(1, total_chars)
        dur = max(0.18, fraction * (total_duration - 0.2))
        words.append({
            "word": w,
            "start": round(current_time, 2),
            "end": round(current_time + dur, 2)
        })
        current_time += dur
    return words

def generate_ass_subtitles(
    audio_path: str,
    output_ass_path: str,
    fallback_text: Optional[str] = None,
    total_duration: float = 45.0,
    font_name: str = "Montserrat ExtraBold",
    font_size: int = 70,
    words_per_chunk: int = 4
) -> str:
    """
    Generates high-retention Advanced SubStation Alpha (.ass) subtitles with word-by-word active highlighting.
    Active word: bright yellow (&H0000FFFF&)
    Inactive words: clean white (&H00FFFFFF&)
    Outline: 3.5px solid black border
    Placement: vertical center / lower third (MarginV=920 for 1080x1920)
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_ass_path)), exist_ok=True)

    words = []
    try:
        words = generate_word_timestamps_whisper(audio_path)
    except Exception as ex:
        print(f"[SubtitleEngine] faster-whisper error: {ex}, using proportional fallback.")
        if fallback_text:
            words = generate_fallback_word_timestamps(fallback_text, total_duration)

    if not words and fallback_text:
        words = generate_fallback_word_timestamps(fallback_text, total_duration)

    header = f"""[Script Info]
Title: High Retention Word Highlight Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,40,40,920,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    # Group words into chunks of 3-4 words for dynamic screen pacing
    chunks = [words[i:i + words_per_chunk] for i in range(0, len(words), words_per_chunk)]

    for chunk in chunks:
        if not chunk:
            continue
        for active_idx, active_item in enumerate(chunk):
            start_str = format_ass_time(active_item["start"])
            end_str = format_ass_time(active_item["end"])

            # Highlight active word in yellow, keep rest white
            styled_words = []
            for idx, item in enumerate(chunk):
                w = item["word"].upper()
                if idx == active_idx:
                    styled_words.append(f"{{\\c&H0000FFFF&}}{w}{{\\c&H00FFFFFF&}}")
                else:
                    styled_words.append(w)

            line_text = " ".join(styled_words)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{line_text}")

    content = header + "\n".join(events) + "\n"
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_ass_path
