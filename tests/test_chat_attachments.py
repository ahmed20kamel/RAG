"""A picture with a marked line, and a recording, become words the question carries.

The picture's whole text is read and each marked region on its own, so the question can
name the exact line the reader circled; the words join the question as one sentence, not
as a second question the analyser would split off and answer separately; a picture with
no writing reads as nothing, rather than as a guess. A recording is turned into text on
this machine — checked only when the speech model is already downloaded.

Offline. Run: python tests/test_chat_attachments.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.exceptions import ValidationError  # noqa: E402
from app.parsers.ocr import capability  # noqa: E402
from app.services.chat_attachments import Mark, attach_picture_text, read_picture  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def png(lines: list[str]) -> bytes:
    picture = Image.new("RGB", (1400, 160 * max(1, len(lines)) + 100), "white")
    draw = ImageDraw.Draw(picture)
    try:
        font = ImageFont.truetype("arial.ttf", 46)
    except OSError:
        font = ImageFont.load_default()
    for index, line in enumerate(lines):
        draw.text((60, 60 + index * 160), line, fill="black", font=font)
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return buffer.getvalue()


def main() -> int:
    analyzer = QueryAnalyzer()

    print("=== 1. the words join the question as one question ===")
    joined = attach_picture_text("ما معنى هذا؟", "INVOICE 5512\nLate penalty: 0.1% per day؟", "Late penalty: 0.1% per day")
    check("Late penalty" in joined and "الجزء المعلَّم" in joined, "the marked line is named in the question", joined)
    check(not analyzer.analyze(joined).multi_part, "…and read as one question, not two parts", joined)
    whole = attach_picture_text("ما هذا؟", "Total due: 48,300 AED", "")
    check("48,300" in whole and "الصورة المرفقة" in whole, "no marks: the picture's text is attached instead", whole)
    check(attach_picture_text("ما هذا؟", "", "") == "ما هذا؟", "a picture with nothing read leaves the question as asked")

    print("\n=== 2. reading a picture ===")
    try:
        read_picture(b"not a picture", [])
        check(False, "something that is not a picture is refused")
    except ValidationError:
        check(True, "something that is not a picture is refused")
    if not capability().available:
        print("[SKIP] OCR is not installed here — the reading checks need it")
    else:
        data = png(["INVOICE No. 5512 - Steel supply", "Late penalty: 0.1% per day", "Total due: 48,300 AED"])
        read = read_picture(data, [Mark(0.02, 0.36, 0.6, 0.2)])
        check("48,300" in read.text and "INVOICE" in read.text, "the whole picture is read", read.text[:80])
        check(len(read.marked) == 1 and "penalty" in read.marked[0].lower() and "48,300" not in read.marked[0],
              "the marked line is read on its own, without its neighbours", str(read.marked))
        blank = read_picture(png([]), [])
        check(not blank.has_text, "a picture with no writing reads as nothing")

    print("\n=== 3. speech, on this machine ===")
    whisper = ROOT / "models" / "whisper"
    if not any(whisper.glob("**/model.bin")):
        print("[SKIP] the speech model is not downloaded here")
    else:
        from app.services.chat_attachments import SpeechToText

        speech = SpeechToText("small", "en", whisper, 120)
        try:
            speech.transcribe(b"", ".webm")
            check(False, "an empty recording is refused")
        except ValidationError:
            check(True, "an empty recording is refused")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
