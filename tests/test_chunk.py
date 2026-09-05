"""Phase-planner tests (TD-16 v2) — computed K, contiguous non-overlapping partition.

All local/offline (killswitch): the planner is pure functions over the saved
transcript text, no model and no key.
"""

from __future__ import annotations

from echogist import chunk
from echogist.config import ChunkConfig


def _timecoded(n_blocks: int, *, step_min: int = 1, words: int = 5) -> str:
    """n_blocks lines of ``[00:MM:00] word word ...`` at ``step_min`` spacing."""
    return "\n".join(f"[00:{m * step_min:02d}:00] " + "word " * words for m in range(n_blocks))


# --------------------------------------------------------------------------- #
# plan_phases (TD-16 v2) — computed K, contiguous, NO overlap, full partition
# --------------------------------------------------------------------------- #
def test_plan_phases_balances_into_k_bins_no_tail() -> None:
    text = _timecoded(12)
    cfg = ChunkConfig(phase_target_tokens=4_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)  # 12 * 1000 = 12000
    # K = ceil(12000 / 4000) = 3, balanced -> 4 / 4 / 4 (same binning math as plan_chunks).
    assert [p.index for p in phases] == [1, 2, 3]
    assert all(p.total == 3 for p in phases)
    assert [len(p.text.splitlines()) for p in phases] == [4, 4, 4]


def test_plan_phases_are_contiguous_and_partition_every_block_once() -> None:
    text = _timecoded(9)
    cfg = ChunkConfig(phase_target_tokens=3_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)
    # No overlap: the phases concatenate back to exactly the original blocks, in order,
    # each block appearing in exactly ONE phase (a clean partition, not overlapping spans).
    rejoined = [line for p in phases for line in p.text.splitlines()]
    assert rejoined == text.splitlines()


def test_plan_phases_no_overlap_phase_two_starts_on_its_own_block() -> None:
    text = _timecoded(6)  # blocks 60s apart
    cfg = ChunkConfig(phase_target_tokens=2_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)  # bins [0,1][2,3][4,5]
    assert len(phases) == 3
    # Unlike a map chunk, phase 2 carries NO prior overlap block — starts at its own 02:00.
    assert phases[1].text.startswith("[00:02:00]")
    assert "[00:01:00]" not in phases[1].text
    assert phases[1].start_seconds == 120.0
    assert phases[1].end_seconds == 180.0


def test_plan_phases_k_scales_with_target_not_capped() -> None:
    # K is COMPUTED from phase_target_tokens, so a smaller target yields more phases —
    # it is not pinned to 3-4. Same 12-block transcript, three targets, three K values.
    text = _timecoded(12)
    est = lambda _line: 1_000  # noqa: E731 — 12 * 1000 = 12000 total
    assert len(chunk.plan_phases(text, ChunkConfig(phase_target_tokens=24_000), estimate=est)) == 1
    assert len(chunk.plan_phases(text, ChunkConfig(phase_target_tokens=6_000), estimate=est)) == 2
    assert len(chunk.plan_phases(text, ChunkConfig(phase_target_tokens=3_000), estimate=est)) == 4


def test_plan_phases_short_text_is_single_phase() -> None:
    phases = chunk.plan_phases(_timecoded(3), ChunkConfig(phase_target_tokens=999_999))
    assert len(phases) == 1
    assert phases[0].index == 1 and phases[0].total == 1


def test_plan_phases_no_timecodes_falls_back_to_one_phase() -> None:
    phases = chunk.plan_phases("no timecodes here at all", ChunkConfig())
    assert len(phases) == 1
    assert phases[0].text == "no timecodes here at all"
    assert phases[0].index == 1 and phases[0].total == 1


def test_plan_phases_single_block_clamps_k_to_one() -> None:
    # A tiny target would ask for many bins, but K is clamped to len(blocks) == 1.
    cfg = ChunkConfig(phase_target_tokens=1)
    phases = chunk.plan_phases("[00:00:00] one", cfg, estimate=lambda _l: 9_999)
    assert len(phases) == 1
    assert phases[0].index == 1 and phases[0].total == 1


def test_plan_phases_span_formats_time_range() -> None:
    text = _timecoded(4)  # 00:00 .. 00:03
    cfg = ChunkConfig(phase_target_tokens=2_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)  # bins [0,1][2,3]
    assert phases[0].span == "00:00:00–00:01:00"
    assert phases[1].span == "00:02:00–00:03:00"


def test_bin_blocks_is_self_defensive_on_degenerate_input() -> None:
    # Unreachable via the public planners (they guard empty blocks), but the shared
    # helper must not divide by zero on a degenerate direct call.
    assert chunk._bin_blocks([], 3) == []
    assert chunk._bin_blocks([1, 2, 3], 0) == []


# --------------------------------------------------------------------------- #
# drop_degenerate_blocks (TD-26) — Whisper boilerplate never reaches the model
# --------------------------------------------------------------------------- #
def _speech(n: int, *, start_min: int = 0) -> list[str]:
    """n blocks of varied, sentence-like text — nothing repeats, so coverage is 0.0."""
    return [
        f"[00:{start_min + i:02d}:00] "
        + " ".join(f"слово{i}{j}" for j in range(40))  # 40 distinct words, ratio 1.0
        for i in range(n)
    ]


_LOOP = "Добро пожаловать на наш канал! Добро пожаловать на наш канал!"  # coverage 0.6
_CREDIT = "Субтитры создавал SubsAuthor"  # 3 words, nothing repeats — coverage 0.0, but tiny


def test_drops_a_repetition_loop() -> None:
    text = "\n".join([f"[00:00:00] {_LOOP}", f"[00:01:00] {_LOOP}", *_speech(3, start_min=2)])
    kept, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert len(dropped) == 2
    assert all(_LOOP in line for line in dropped)
    assert len(kept.splitlines()) == 3


def test_drops_a_short_credit_line_touching_a_loop() -> None:
    """Rule B: the 3-word credit repeats nothing, so only adjacency catches it."""
    text = "\n".join(
        [
            f"[00:00:00] {_CREDIT}",
            f"[00:01:00] {_LOOP}",
            f"[00:02:00] {_LOOP}",
            *_speech(3, start_min=3),
        ]
    )
    kept, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert _CREDIT in dropped[0], "the credit line adjacent to the loop must go with it"
    assert len(dropped) == 3
    assert len(kept.splitlines()) == 3


def test_keeps_a_short_block_surrounded_by_speech() -> None:
    """A quiet minute is not boilerplate: length alone must never cut a block."""
    quiet = "[00:02:00] Да, именно так."
    text = "\n".join([*_speech(2), quiet, *_speech(2, start_min=3)])
    kept, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert dropped == ()
    assert quiet in kept


def test_keeps_a_block_that_is_mostly_real_speech() -> None:
    """WHOLE blocks only — a stray credit glued to real talk keeps the real talk."""
    mixed = "[00:01:00] " + _CREDIT + " " + " ".join(f"мысль{j}" for j in range(40))
    text = "\n".join([*_speech(1), mixed, *_speech(1, start_min=2)])
    kept, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert dropped == ()
    assert mixed in kept


def test_all_boilerplate_is_returned_untouched_not_emptied() -> None:
    """A transcript with no speech at all must not become an empty synthesis input."""
    text = "\n".join(f"[00:0{i}:00] {_LOOP}" for i in range(3))
    kept, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert kept == text
    assert dropped == ()


def test_drop_is_configurable_off() -> None:
    """A coverage ceiling above 1.0 disables Rule A, and with it Rule B's only seed."""
    text = "\n".join([f"[00:00:00] {_LOOP}", *_speech(2, start_min=1)])
    kept, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig(max_loop_coverage=1.01))
    assert dropped == ()
    assert kept == text


# --------------------------------------------------------------------------- #
# TD-33 — the discriminator tells a stuck decoder from a lecturer making a point
# --------------------------------------------------------------------------- #
#: A Socratic drill from the operator's real lecture 1 at [01:12:24]. The answer IS a
#: repeated phrase, five times, because that is the teaching device. The unique-word ratio
#: this replaced scored it 0.550 against a 0.55 floor and deleted the whole minute.
_RHETORICAL = (
    "Главный проект? Буквально что? Сил, опыта, терпения? Пока я по десять часов "
    "в неделю трачу на дорогу. Сил, опыта, терпения, правильно? Да, сил, опыта, "
    "терпения. Чтобы начать несколько проектов. Чего не хватает? Того же самого. "
    "Сил, опыта, терпения. Чтобы начать первый проект, чего не хватает? "
    "Сил, опыта, терпения."
)


def test_rhetorical_repetition_is_speech_and_is_kept() -> None:
    """The TD-33 defect, verbatim from a real lecture.

    A phrase repeated to make a point is scattered among varied sentences; a decoder loop
    blankets the block. Measured over 13 lectures, the old ratio deleted 39 such blocks
    (5172 words of lecture) to catch 18 loops.
    """
    text = "\n".join([*_speech(1), f"[00:01:00] {_RHETORICAL}", *_speech(1, start_min=2)])
    kept, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert dropped == ()
    assert _RHETORICAL in kept


def test_a_verbatim_decoder_loop_is_still_dropped() -> None:
    """The other side of the same line: one phrase, over and over, covering everything."""
    loop = "[00:01:00] " + "I love you, " * 40
    text = "\n".join([*_speech(1), loop, *_speech(1, start_min=2)])
    _, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert dropped == (loop,)


def test_a_two_repeat_credit_line_is_caught_on_its_own() -> None:
    """TD-26's original boilerplate, six words. A 3-gram window sees it without adjacency;
    a longer window could not, which is why the window is 3."""
    doubled = f"[00:01:00] {_CREDIT} {_CREDIT}"
    text = "\n".join([*_speech(1), doubled, *_speech(1, start_min=2)])
    _, dropped = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert dropped == (doubled,)


def test_coverage_counts_positions_not_occurrences() -> None:
    """Consecutive windows of a loop OVERLAP. Counting occurrences times n would report
    more than 100% of a block that is a single phrase, so coverage is a position set."""
    assert chunk._loop_coverage(["раз", "два", "три"] * 10) == 1.0
    assert chunk._loop_coverage(["а", "б", "в", "г", "д", "е", "ж", "з", "и", "к"]) == 0.0


def test_kept_blocks_keep_their_timecodes_for_the_anchor_gate() -> None:
    """Anchors are validated against the text the model saw, so surviving timecodes must
    still parse — a dropped block simply stops being a citeable anchor."""
    text = "\n".join([f"[00:00:00] {_LOOP}", *_speech(2, start_min=1)])
    kept, _ = chunk.drop_degenerate_blocks(text, ChunkConfig())
    assert chunk.block_timecodes(kept) == ("[00:01:00]", "[00:02:00]")
