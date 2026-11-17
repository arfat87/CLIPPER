from __future__ import annotations

import json
import subprocess
from typing import Any

import pysubs2

from backend.app.config import get_settings

SAMPLE_SPECS: list[dict[str, Any]] = [
    {
        "id": "sample_hinglish_podcast",
        "title": "Hinglish Creator Podcast: Viral Growth & Mindset Secrets",
        "language": "hinglish",
        "script": "latn",
        "duration": 90.0,
        "bg_color": "0x1e293b",
        "segments": [
            {
                "start": 1.0,
                "end": 14.0,
                "text": "Welcome back dosto, aaj hum baat karenge content creation ke sabse bade secret ke baare mein.",
                "energy": 0.55,
            },
            {
                "start": 15.0,
                "end": 38.0,
                "text": "99 percent creators fail isliye hote hain kyunki woh pehle teen second mein hook nahi dete! Agar aapka opening boring hai toh koi rukega hi nahi, samjhe?",
                "energy": 0.92,
            },
            {
                "start": 40.0,
                "end": 62.0,
                "text": "Doosra golden rule simple hai: consistency se zyada zaroori hai har video mein ek clear takeaway dena jo viewer turant share kare.",
                "energy": 0.85,
            },
            {
                "start": 64.0,
                "end": 88.0,
                "text": "Jab maine apna pehla viral short banaya, tab mujhe samajh aaya ki storytelling aur clean captions hi asli game changer hain!",
                "energy": 0.88,
            },
        ],
    },
    {
        "id": "sample_english_keynote",
        "title": "AI Systems Keynote: Why Speed and Focus Win",
        "language": "en",
        "script": "latn",
        "duration": 75.0,
        "bg_color": "0x0f172a",
        "segments": [
            {
                "start": 1.0,
                "end": 18.0,
                "text": "Here is the brutal truth nobody tells founders: shipping fast beats perfection every single time in the first year.",
                "energy": 0.91,
            },
            {
                "start": 20.0,
                "end": 44.0,
                "text": "When you obsess over tiny details before talking to users, you are optimizing a product that nobody asked for.",
                "energy": 0.82,
            },
            {
                "start": 46.0,
                "end": 72.0,
                "text": "The best teams build a tight feedback loop, measure real retention, and double down on the one feature users love!",
                "energy": 0.89,
            },
        ],
    },
    {
        "id": "sample_hindi_motivation",
        "title": "Hindi Story: सफलता का असली रहस्य",
        "language": "hi",
        "script": "deva",
        "duration": 65.0,
        "bg_color": "0x172554",
        "segments": [
            {
                "start": 1.0,
                "end": 21.0,
                "text": "सफलता एक दिन में नहीं मिलती दोस्तों, लेकिन अगर आप हर दिन एक प्रतिशत बेहतर बनते हैं तो एक साल में आपकी ज़िंदगी बदल जाएगी!",
                "energy": 0.90,
            },
            {
                "start": 23.0,
                "end": 45.0,
                "text": "डर सबको लगता है, पर विजेता वही होता है जो डर के बावजूद पहला कदम उठाता है।",
                "energy": 0.84,
            },
            {
                "start": 47.0,
                "end": 63.0,
                "text": "आज ही अपने लक्ष्य को लिखिए और बिना रुके उस पर काम शुरू कीजिए!",
                "energy": 0.87,
            },
        ],
    },
    {
        "id": "sample_bengali_tech",
        "title": "Bangla Creator Talk: কনটেন্ট তৈরির সেরা কৌশল",
        "language": "bn",
        "script": "beng",
        "duration": 65.0,
        "bg_color": "0x14532d",
        "segments": [
            {
                "start": 1.0,
                "end": 22.0,
                "text": "আপনি কি জানেন কেন কিছু ভিডিও মুহূর্তের মধ্যে ভাইরাল হয়ে যায়? কারণ প্রথম তিন সেকেন্ডেই দর্শকের মনোযোগ ধরে রাখা হয়!",
                "energy": 0.91,
            },
            {
                "start": 24.0,
                "end": 45.0,
                "text": "সহজ ভাষায় গল্প বলা এবং পরিষ্কার ক্যাপশন ব্যবহার করলে দর্শক শেষ পর্যন্ত ভিডিও দেখে।",
                "energy": 0.83,
            },
            {
                "start": 47.0,
                "end": 63.0,
                "text": "তাই আজ থেকেই আপনার প্রতিটি শর্ট ভিডিওতে দারুণ হুক এবং সাবটাইটেল যোগ করুন!",
                "energy": 0.88,
            },
        ],
    },
]


def _build_words_for_segment(seg: dict[str, Any], seg_id: int) -> dict[str, Any]:
    raw_words = seg["text"].split()
    start = float(seg["start"])
    end = float(seg["end"])
    dur = max(0.5, end - start)
    step = dur / max(1, len(raw_words))
    words = []
    for idx, w in enumerate(raw_words):
        ws = round(start + idx * step, 2)
        we = round(min(end, ws + step * 0.92), 2)
        words.append({"w": w, "s": ws, "e": we, "p": 0.96})
    return {
        "id": seg_id,
        "start": start,
        "end": end,
        "text": seg["text"],
        "energy": seg.get("energy", 0.75),
        "words": words,
    }


def ensure_sample_media() -> list[dict[str, Any]]:
    """Generate lightweight 16:9 MP4 sample videos + word-level fixture JSONs for instant testing."""
    settings = get_settings()
    samples_dir = settings.assets_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    for spec in SAMPLE_SPECS:
        sid = spec["id"]
        mp4_path = samples_dir / f"{sid}.mp4"
        json_path = samples_dir / f"{sid}.transcript.json"
        duration = float(spec["duration"])

        segments = [
            _build_words_for_segment(s, idx) for idx, s in enumerate(spec["segments"])
        ]
        transcript_data = {
            "id": sid,
            "title": spec["title"],
            "language": spec["language"],
            "script": spec["script"],
            "duration": duration,
            "segments": segments,
        }
        json_path.write_text(
            json.dumps(transcript_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if not mp4_path.exists():
            # Generate a fast synthetic 16:9 video with moving focal box + modulated sine audio
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={spec['bg_color']}:s=1280x720:r=30:d={duration}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=220:sample_rate=16000:duration={duration}",
                "-vf",
                "drawgrid=w=160:h=160:t=2:c=white@0.12,drawbox=x='(iw-360)/2+sin(t)*140':y='(ih-420)/2':w=360:h=420:color=0x38bdf8@0.35:t=fill",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                str(mp4_path),
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        manifest.append(
            {
                "id": sid,
                "url": f"sample://{sid}",
                "title": spec["title"],
                "language": spec["language"],
                "script": spec["script"],
                "duration": duration,
                "mp4_path": str(mp4_path),
                "transcript_path": str(json_path),
            }
        )

    return manifest


def verify_toolchain() -> dict[str, Any]:
    """Verify FFmpeg, libass, harfbuzz/fribidi, and test Indic (Hindi + Bengali) subtitle burn-in."""
    res: dict[str, Any] = {
        "ffmpeg_available": False,
        "libass_enabled": False,
        "fribidi_enabled": False,
        "indic_render_ok": False,
    }
    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, check=True
        )
        out = proc.stdout
        res["ffmpeg_available"] = True
        res["libass_enabled"] = "--enable-libass" in out
        res["fribidi_enabled"] = "--enable-libfribidi" in out
    except Exception as exc:
        res["error"] = str(exc)
        return res

    # Run a 1-second test render burning English + Hindi + Bengali ASS subtitles
    settings = get_settings()
    tmp_dir = settings.data_dir / "healthcheck"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ass_path = tmp_dir / "indic_test.ass"
    out_mp4 = tmp_dir / "indic_test.mp4"

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "540"
    subs.info["PlayResY"] = "960"
    style = pysubs2.SSAStyle(
        fontname="Sans",
        fontsize=40,
        primarycolor=pysubs2.Color(255, 230, 0, 0),
        outlinecolor=pysubs2.Color(0, 0, 0, 0),
        outline=3.0,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
    )
    subs.styles["Default"] = style
    subs.events.append(
        pysubs2.SSAEvent(
            start=0,
            end=1000,
            text="English · नमस्ते हिन्दी · বাংলা ক্যাপশন",
        )
    )
    subs.save(str(ass_path))

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x0f172a:s=540x960:r=10:d=1",
                "-vf",
                f"ass={ass_path}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(out_mp4),
            ],
            check=True,
            capture_output=True,
        )
        res["indic_render_ok"] = out_mp4.exists() and out_mp4.stat().st_size > 1000
    except Exception as exc:
        res["render_error"] = str(exc)

    return res


if __name__ == "__main__":
    print("Verifying toolchain...", verify_toolchain())
    print("Generating sample media...", len(ensure_sample_media()), "samples ready.")
