"""What a person shows or says to the chat, turned into words the question can carry.

* **A picture, marked.** A screenshot, a photo of a page, a quotation — with the part
  they mean circled. The whole picture is read, and each marked part on its own, so the
  question can say exactly which line it is about. Text is what is read: a picture with
  no writing in it (a crack in a wall) yields nothing, and the reader is told so rather
  than shown a guess.

* **A recording.** Speech turned into text on this machine. The browser's own dictation
  sends the audio to an outside service, which nothing here may do.

Neither the picture nor the recording is kept: only the words taken from them travel with
the question.
"""

from __future__ import annotations

import io
import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from app.exceptions import ValidationError

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
#: Around each mark, so a circle drawn tight around a line still contains all of it.
MARK_PADDING = 0.02
#: How much picture text travels with a question. The marked part always goes first.
MAX_ATTACHED_CHARS = 1500


@dataclass
class Mark:
    """A marked region, as fractions of the picture's width and height."""

    x: float
    y: float
    w: float
    h: float


@dataclass
class ReadPicture:
    text: str
    marked: list[str]

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip() or any(m.strip() for m in self.marked))


def read_picture(data: bytes, marks: list[Mark]) -> ReadPicture:
    from PIL import Image, ImageOps

    from app.parsers.ocr import read_image

    if not data:
        raise ValidationError("الصورة فارغة.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError("الصورة أكبر من 12 ميغابايت.")
    try:
        picture = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - anything PIL cannot open is not a picture
        raise ValidationError("تعذّر فتح الصورة. أرسل صورة PNG أو JPG.") from exc

    marked: list[str] = []
    for mark in marks[:6]:
        left = max(0.0, mark.x - MARK_PADDING) * picture.width
        top = max(0.0, mark.y - MARK_PADDING) * picture.height
        right = min(1.0, mark.x + mark.w + MARK_PADDING) * picture.width
        bottom = min(1.0, mark.y + mark.h + MARK_PADDING) * picture.height
        if right - left < 8 or bottom - top < 8:
            continue
        marked.append(read_image(picture.crop((round(left), round(top), round(right), round(bottom)))))
    return ReadPicture(text=read_image(picture), marked=[m for m in marked if m.strip()])


def attach_picture_text(question: str, text: str, marked: str) -> str:
    """The question with what the picture says, as one sentence — not as a second
    question the analyser would split off and answer separately."""
    def flat(value: str) -> str:
        return " ".join(value.replace("؟", " ").replace("?", " ").split())

    marked, text = flat(marked), flat(text)
    body = question.strip().rstrip("؟?. ")
    if marked:
        return f"{body} بخصوص الجزء المعلَّم في الصورة المرفقة: «{marked[:MAX_ATTACHED_CHARS]}»؟"
    if text:
        return f"{body} بخصوص الصورة المرفقة التي فيها: «{text[:MAX_ATTACHED_CHARS // 2]}»؟"
    return question


class SpeechToText:
    """faster-whisper, loaded on first use and kept. One recording at a time: a second
    model instance would double the memory, and recordings are seconds long."""

    def __init__(self, model: str, language: str, models_dir: Path, max_seconds: int) -> None:
        self.model_name = model
        self.language = language or None
        self.models_dir = models_dir
        self.max_seconds = max_seconds
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self.models_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Loading speech model %s (first use)", self.model_name)
            self._model = WhisperModel(
                self.model_name, device="cpu", compute_type="int8", download_root=str(self.models_dir)
            )
        return self._model

    def transcribe(self, data: bytes, suffix: str = ".webm") -> str:
        if not data:
            raise ValidationError("التسجيل فارغ.")
        if len(data) > MAX_AUDIO_BYTES:
            raise ValidationError("التسجيل أطول من المسموح.")
        # Decoded from a file: the container formats browsers record in (webm, ogg, mp4)
        # are read by the decoder from a path more reliably than from a stream.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
            path = Path(handle.name)
        try:
            with self._lock:
                segments, info = self._load().transcribe(
                    str(path), language=self.language, vad_filter=True, beam_size=5,
                )
                text = " ".join(segment.text.strip() for segment in segments).strip()
            logger.info("Transcribed %.1fs of speech (%s) into %s chars", info.duration, info.language, len(text))
            return text
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken recording is the reader's to redo
            logger.exception("Speech recognition failed")
            raise ValidationError("تعذّر تحويل التسجيل إلى نص. حاول مرة أخرى.") from exc
        finally:
            path.unlink(missing_ok=True)
