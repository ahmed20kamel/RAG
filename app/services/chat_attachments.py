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
    #: What a vision model understood the picture to be and to show.
    vision: str = ""

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip() or self.vision.strip() or any(m.strip() for m in self.marked))


VISION_PROMPT = (
    "أرسل مستخدم هذه الصورة في محادثة مع نظام يجيب من مستندات شركة مقاولات. صف ما فيها لمن لا يراها، بالعربية:\n"
    "1) ما نوعها في سطر واحد (لقطة شاشة لمحادثة أو برنامج، جدول، مستند، رسم هندسي، صورة موقع، رسالة خطأ…).\n"
    "2) أهم ما تعرضه: النصوص والأرقام والعناوين الظاهرة، حرفيًا قدر الإمكان، والجداول كجدول Markdown.\n"
    "3) إن كانت فيها رسالة خطأ أو تنبيه أو حالة نظام، فاذكرها بوضوح.\n"
    "لا تذكر شيئًا غير ظاهر في الصورة، ولا تستنتج ما لا تراه."
)
#: How long a picture waits for the model before it settles for OCR alone.
VISION_WAIT_SECONDS = 45
VISION_MARKED_NOTE = "\nالصورة الثانية جزء من الأولى علّمه المستخدم بنفسه: ابدأ بوصفه، فهو ما يقصده."


def describe_picture(picture, marked_crops: list, base_url: str, model: str, timeout: float = 120.0) -> str:
    """What a vision model sees in the picture, or an empty string when it is not available.

    Passed through the model gate like any other model call: the vision model shares the
    graphics card, and two models loading at once would slow both.
    """
    import base64

    import httpx

    from app.services.model_gate import MODEL_GATE

    def encode(image) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        return base64.b64encode(buffer.getvalue()).decode()

    images = [encode(picture)] + [encode(crop) for crop in marked_crops[:1]]
    prompt = VISION_PROMPT + (VISION_MARKED_NOTE if len(images) > 1 else "")
    try:
        # Not behind a long answer: a picture read by OCR alone, now, beats one understood
        # fifteen minutes later.
        MODEL_GATE.acquire(timeout=VISION_WAIT_SECONDS)
    except TimeoutError:
        logger.info("The model is busy; the picture is read by OCR alone")
        return ""
    try:
        response = httpx.post(f"{base_url.rstrip('/')}/api/chat", json={
            "model": model, "stream": False, "keep_alive": "10m",
            "options": {"temperature": 0.1, "num_ctx": 8192},
            "messages": [{"role": "user", "content": prompt, "images": images}],
        }, timeout=timeout)
        response.raise_for_status()
        return ((response.json().get("message") or {}).get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001 - the picture is still read by OCR
        # Typically the text model is busy and cannot be moved off the graphics card to
        # load this one: measured, the request then waits until it times out. Two minutes
        # at most, and the picture's text — read by OCR — still answers the question.
        logger.warning("The vision model could not describe the picture (%s); OCR alone", type(exc).__name__)
        return ""
    finally:
        MODEL_GATE.release()


def read_picture(data: bytes, marks: list[Mark], vision: tuple[str, str] | None = None) -> ReadPicture:
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
    crops = []
    for mark in marks[:6]:
        left = max(0.0, mark.x - MARK_PADDING) * picture.width
        top = max(0.0, mark.y - MARK_PADDING) * picture.height
        right = min(1.0, mark.x + mark.w + MARK_PADDING) * picture.width
        bottom = min(1.0, mark.y + mark.h + MARK_PADDING) * picture.height
        if right - left < 8 or bottom - top < 8:
            continue
        crop = picture.crop((round(left), round(top), round(right), round(bottom)))
        crops.append(crop)
        marked.append(read_image(crop))
    seen = describe_picture(picture, crops, *vision) if vision else ""
    return ReadPicture(text=read_image(picture), marked=[m for m in marked if m.strip()], vision=seen)


#: How much of the picture's text joins the question itself — enough for the search to
#: find the document it came from. All of it goes to the model as evidence.
SEARCH_EXCERPT_CHARS = 300
#: The evidence a picture becomes: what the model reads and cites as «الصورة المرفقة».
PICTURE_DOCUMENT_ID = "attached-picture"


def _flat(value: str) -> str:
    return " ".join(value.replace("؟", " ").replace("?", " ").split())


def attach_picture_text(question: str, text: str, marked: str, english: bool = False) -> str:
    """The question with what the picture says, as one sentence — not as a second
    question the analyser would split off and answer separately — and joined in the
    question's own language, or an English question is answered in Arabic."""
    marked, text = _flat(marked), _flat(text)
    body = question.strip().rstrip("؟?. ")
    end = "?" if english else "؟"
    if marked:
        lead = " regarding the marked part of the attached picture:" if english else " بخصوص الجزء المعلَّم في الصورة المرفقة:"
        return f"{body}{lead} «{marked[:SEARCH_EXCERPT_CHARS]}»{end}"
    if text:
        lead = " regarding the attached picture, which reads:" if english else " بخصوص الصورة المرفقة التي فيها:"
        return f"{body}{lead} «{text[:SEARCH_EXCERPT_CHARS]}»{end}"
    return question


def picture_evidence(text: str, marked: str, english: bool = False, vision: str = ""):
    """The picture as a passage the answer may quote and cite, the marked part first.

    The reader showed it in order to be answered from it: kept out of the evidence, the
    prices plainly visible in a screenshot were "not in the sources".
    """
    from app.core.retrieval import Candidate

    body = "\n\n".join(part for part in (
        (("The marked part: " if english else "الجزء المعلَّم: ") + marked.strip()) if marked.strip() else "",
        (("What the picture shows (vision model): " if english else "ما تعرضه الصورة (قراءة نموذج الرؤية): ")
         + vision.strip()) if vision.strip() else "",
        (("Its text, read word for word (OCR): " if english else "نصها المنسوخ حرفيًا (OCR): ") + text.strip())
        if text.strip() else "",
    ) if part)
    if not body:
        return None
    name = "Attached picture" if english else "الصورة المرفقة"
    candidate = Candidate(
        chunk_id=PICTURE_DOCUMENT_ID, document_id=PICTURE_DOCUMENT_ID, filename=name,
        document_title=name, section=name, section_id=PICTURE_DOCUMENT_ID, heading=name,
        parent_section="", content=body,
    )
    candidate.rerank_score = 1.0
    candidate.fused_score = 1.0
    # Ranked as direct evidence: without an origin it fell to the last tier, after
    # every document passage, where the context budget could cut it — and a question
    # about the picture alone was refused with the picture's text never shown.
    candidate.origins = {"vector", "picture"}
    return candidate


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
