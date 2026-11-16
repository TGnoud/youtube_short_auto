"""Shared animated subtitle style for image Shorts and MP4 voiceovers."""
from pathlib import Path


def ass_clock(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    secs, cs = divmod(cs, 100)
    return f'{hours}:{minutes:02}:{secs:02}.{cs:02}'


def make_animated_subtitles(lines, target: Path, width=1080, height=1920):
    scale = min(width, height) / 1080
    font = max(14, round(84 * scale))
    outline = max(1, round(5.5 * scale, 2))
    margin_x = max(12, round(60 * width / 1080))
    # Center the caption slightly below the screen midpoint on every aspect ratio.
    anchor = rf'{{\an5\pos({width / 2:g},{height * 0.60:g})}}'
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{font},&H0000D7FF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,5,{margin_x},{margin_x},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for start, end, text in lines:
        words = [w.replace('\\', '').replace('{', '').replace('}', '') for w in text.split()]
        start_cs, end_cs = round(start * 100), round(end * 100)
        if not words or end_cs <= start_cs:
            continue
        # Approximate word timing, not forced alignment to speech.
        weights = [max(1, len(w)) for w in words]
        total_cs = end_cs - start_cs
        boundaries = [round(total_cs * sum(weights[:i]) / sum(weights)) for i in range(len(words) + 1)]
        # Karaoke fill sweeps from white to yellow within each word, keeping
        # the entire caption at a fixed size and already-read text yellow.
        chunks = [
            r'{\kf' + str(boundaries[i + 1] - boundaries[i]) + '}' + word
            for i, word in enumerate(words)
        ]
        events.append(f'Dialogue: 0,{ass_clock(start_cs / 100)},{ass_clock(end_cs / 100)},Default,,0,0,0,,{anchor}' + ' '.join(chunks))
    target.write_text(header + '\n'.join(events) + '\n', encoding='utf-8')
