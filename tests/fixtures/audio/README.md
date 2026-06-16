# Measurement clips (T11 / TD-4)

The T11 harness (`scripts/measure_model.py`) needs two short reference clips for
the int8-vs-float16 A/B. They are **not committed** (binary audio; also
huggingface.co / large media is unreachable from the WSL dev env). Place them
here on the Windows 4060 box before running the measurement:

| File          | What                                  |
|---------------|---------------------------------------|
| `ru.<ext>`    | ~3–5 min Russian speech (the RU quality reference). |
| `en.<ext>`    | ~3–5 min English speech (the EN control).           |

Any container ffmpeg reads works (`.mp3`, `.m4a`, `.wav`, `.mp4`, ...). The
harness picks the first `ru.*` / `en.*` it finds, or take explicit paths:

```
python scripts/measure_model.py --ru path\to\ru.mp3 --en path\to\en.mp3
```

Pick clips you can judge the transcription quality of by eye — the RU clip is the
one whose int8-vs-float16 output you read to close TD-4. Keep the same two clips
across runs so the numbers stay comparable.
