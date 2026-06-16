# Model measurement — large-v3-int8_float16

- Date: 2026-06-16
- Host: RTX 4060
- Bar: 3.00x realtime
- Method: warm (model load + one warmup pass excluded from the timed region).

## Speed + VRAM

| compute_type | clip | audio | wall | realtime_factor | peak VRAM |
|---|---|---:|---:|---:|---:|
| int8_float16 | RU | 00:03:43 | 00:00:22 | 10.11x | 3464 MB |
| int8_float16 | EN | 00:03:36 | 00:00:37 | 5.79x | 4008 MB |
| float16 | RU | 00:03:43 | 00:00:21 | 10.27x | 5387 MB |
| float16 | EN | 00:03:36 | 00:00:32 | 6.76x | 5547 MB |

## Speed verdict (auto)

PASS: int8_float16 realtime_factor 10.11x >= bar 3.00x.

## RU quality A/B — operator read

### int8_float16 — RU

```
[transcript of a third-party clip removed before publication]
```

### float16 (control) — RU

```
[transcript of a third-party clip removed before publication]
```

## OPERATOR VERDICT (closes TD-4)

- [x] Keep int8_float16 (default) — RU quality >= the float16 control.

Reason: int8 RU quality is BETTER than float16 control on this run — float16
produced garbled text in the first paragraph ("ехали до себя хорошо для ничего
не теряю", "мог есть" instead of "бог есть") while int8 transcribed correctly.
Speed 10.11x, VRAM 3.5 GB vs 5.4 GB. int8_float16 confirmed as default with no
reservations.
