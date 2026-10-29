"""Text-to-Speech (Windows SAPI)."""
from __future__ import annotations
import logging
import os
import subprocess

log = logging.getLogger("autodj")


def synth(text: str, out_wav: str, cfg) -> None:
    """Synthesize `text` to `out_wav` using Windows SAPI (PowerShell)."""
    if os.name != "nt":
        raise RuntimeError("TTS only available on Windows")
    ps_text = text.replace("'", "''")
    parts = [
        "Add-Type -AssemblyName System.Speech;",
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;",
        f"$s.SetOutputToWaveFile('{out_wav}');",
    ]
    if cfg.tts_voice:
        parts.append(f"$s.SelectVoice('{cfg.tts_voice}');")
    if cfg.tts_rate:
        parts.append(f"$s.Rate = {cfg.tts_rate};")
    parts.append(f"$s.Speak('{ps_text}');")
    parts.append("$s.Dispose();")
    ps_script = " ".join(parts)
    full_cmd = ["powershell", "-NoProfile", "-Command", ps_script]
    try:
        subprocess.run(full_cmd, check=True, capture_output=True, timeout=30,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.CalledProcessError as e:
        log.error("TTS failed: %s", e.stderr.decode() if e.stderr else e)
        raise
    except subprocess.TimeoutExpired:
        log.error("TTS timed out")
        raise