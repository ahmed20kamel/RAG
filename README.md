# RAG Knowledge Base — Markdown MVP

نظام قاعدة معرفة يعمل محليًا بالكامل: يرفع ملفات Markdown، يحللها، يقسمها حسب العناوين،
ينشئ Embeddings، يخزنها في Qdrant، ثم يجيب عن الأسئلة عبر Ollama مع إظهار المصدر والقسم.

المرحلة الحالية تدعم `.md` و `.markdown` فقط.

## المتطلبات

| المكوّن | الإصدار المستخدم |
|---|---|
| Python | 3.11+ |
| Docker | لتشغيل Qdrant |
| Ollama | نموذج محادثة + نموذج embeddings |

## التشغيل

```bash
# 1. Qdrant
docker compose up -d

# 2. نماذج Ollama
ollama pull qwen3            # نموذج الإجابة
ollama pull bge-m3           # نموذج الـEmbeddings (متعدد اللغات، 1024 بُعد)

# 3. البيئة
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

# 4. التشغيل
python run.py
```

الواجهة: <http://localhost:8000> — وتوثيق API: <http://localhost:8000/docs>

## البنية

**عند الرفع** (كل التحليل الثقيل يحدث هنا، مرة واحدة):

```
Document → Parse → Structure Analysis → Entity Extraction → Section Summaries
        → Smart Chunking → Knowledge Index (SQLite) → Embeddings → Qdrant → BM25 Index
```

**عند السؤال** (بلا أي تحليل للمستند، وباستدعاء واحد للنموذج):

```
Question → Query Analysis (قواعد، بلا LLM)
        → Vector Search ⊕ BM25 Keyword Search ⊕ Entity Lookup
        → RRF Fusion → Rerank → Section Expansion → Rerank
        → Tiered Context (أساسي/مساند/ذو صلة)
        → Ollama → Answer + Citations → Validation
```

```
app/
├── config.py                 إعدادات من متغيرات البيئة فقط
├── container.py              نقطة تركيب كل الخدمات
├── core/
│   ├── domain.py             كائنات المجال (Section, Entity, Chunk, Status)
│   ├── retrieval.py          Candidate + طبقات الأدلة
│   └── text.py               تطبيع وتجزئة عربية (أساس البحث بالكلمات)
├── parsers/
│   ├── base.py               DocumentParser (ABC) + ParserRegistry
│   └── markdown_parser.py    MarkdownParser
├── services/
│   ├── structure.py          شجرة الأقسام والعلاقات ونوع المستند
│   ├── entities.py           استخراج الكيانات (أنماط حتمية، بلا LLM)
│   ├── summaries.py          ملخصات استخراجية للأقسام
│   ├── chunking.py           تقسيم يحترم العناوين والجداول والأكواد
│   ├── knowledge_store.py    قراءة/كتابة فهرس المعرفة
│   ├── keyword_index.py      BM25 على نص المقاطع
│   ├── embeddings.py         عميل Ollama للـEmbeddings
│   ├── vector_store.py       Qdrant
│   ├── ingestion.py          خط المعالجة + حالات التقدم
│   ├── document_service.py   دورة حياة المستند
│   ├── query_analysis.py     فهم السؤال (قواعد، بلا LLM)
│   ├── retriever.py          استرجاع هجين + توسيع الأقسام
│   ├── reranking.py          Reranker (واجهة) + FeatureReranker
│   ├── context_builder.py    سياق مُطبَّق + استشهادات
│   ├── answer_validation.py  فحص الاكتمال والتقييد
│   └── rag_service.py        التنسيق العام
├── models/                   سجل المستندات + فهرس المعرفة (SQLite)
├── schemas/                  عقود الطلب والاستجابة
├── api/routes/               documents, chat, health
└── static/                   لوحة التحكم
```

### لماذا استرجاع هجين؟

البحث الشعاعي وحده كان سبب الإجابات الناقصة: فهو يرتّب بالمعنى، فيضيّع المقطع الذي
يحمل رقم عقد أو رقم رخصة، ويكتفي بقسم واحد عندما تكون الإجابة موزعة. النظام الآن
يجمع ثلاثة مسارات مستقلة للعثور على المقطع:

| المسار | يلتقط |
|---|---|
| Vector (bge-m3) | التشابه في المعنى |
| BM25 | المطابقة الحرفية: `B1N-2024-005221-P01`، `CN-0000000`، الأسماء |
| Entity Lookup | الأقسام التي تحتوي حقائق مستخرجة تطابق صياغة السؤال |

تُدمج النتائج بـ Reciprocal Rank Fusion، ثم يُعاد ترتيبها، ثم يوسَّع البحث إلى:

1. **بقية مقاطع نفس القسم** — لأن القسم الطويل (كجدول التسلسل الزمني) يُقسَّم إلى
   عدة مقاطع، وبقية الإجابة غالبًا في المقطع المجاور من القسم نفسه.
2. **الأقسام الأب/الأبناء/الأشقاء** — عندما يحتاج السؤال أكثر من قسم.
3. **بحث موجَّه بالكلمات غير المغطاة** — إن بقيت مصطلحات من السؤال بلا تغطية.

ثم تُحجز مقاعد (`RESERVED_SEMANTIC_SLOTS`) لأقوى التطابقات الدلالية. هذه الحماية
ضرورية: السؤال العربي عن معلومة موجودة في مستند إنجليزي لا يطابق لفظيًا، فتتفوق
عليه مقاطع عربية تشترك معه في كلمات عامة فقط، ويدفنه إعادة الترتيب رغم أنه أقرب
تطابق دلالي في المجموعة كلها.

### لماذا لا يوجد LLM في التحليل؟

على هذا الجهاز يستغرق التوليد أكثر من 70 ثانية. لذلك تحليل السؤال واستخراج الكيانات
وتلخيص الأقسام والتحقق من الإجابة كلها **حتمية بالقواعد**، فلا تضيف زمنًا يُذكر —
ولا يمكنها اختلاق معلومة أصلًا. الاستدعاء الوحيد للنموذج هو توليد الإجابة النهائية.

## دورة معالجة المستند

```
Uploaded → Parsing → Chunking → Embedding → Indexing → Completed
                                                     ↘ Failed + رسالة الخطأ
```

المعالجة تتم في خيوط خلفية، فيعود الرفع فورًا بحالة `202 Accepted` وتتابع الواجهة التقدم.

## استراتيجية التقسيم

* العناوين هي الحدود الأساسية، وكل chunk يحتفظ بمساره الكامل (`مستند → قسم → قسم فرعي`).
* الكتل الذرية لا تُقطع: Code Blocks، الجداول، القوائم.
* الأقسام الكبيرة تُقسم مع تداخل (`CHUNK_OVERLAP`) للحفاظ على السياق.
* الأقسام الصغيرة تُدمج مع شقيقها تحت نفس الأب فقط، حفاظًا على دقة الاستشهاد.
* نص الـEmbedding يُسبق بعنوان المستند ومسار القسم ليبقى المقطع مفهومًا منفردًا.

## API

| Method | Endpoint | الوظيفة |
|---|---|---|
| POST | `/api/documents/upload` | رفع ملف Markdown وبدء المعالجة |
| GET | `/api/documents` | قائمة المستندات (فلترة: status, category, search) |
| GET | `/api/documents/{id}` | تفاصيل المستند مع الـchunks |
| GET | `/api/documents/{id}/sections` | شجرة الأقسام مع الملخصات والمصطلحات |
| GET | `/api/documents/{id}/entities` | الحقائق المستخرجة حرفيًا (فلترة بـ `kind`) |
| GET | `/api/documents/categories` | التصنيفات المستخدمة |
| POST | `/api/documents/{id}/reindex` | إعادة الفهرسة من الملف المخزن دون إعادة رفع |
| DELETE | `/api/documents/{id}` | حذف المستند ومتجهاته |
| GET | `/api/documents/stats` | إجماليات المكتبة (تُحسب في قاعدة البيانات لا في العميل) |
| GET | `/api/documents/{id}/chunks` | صفحة من المقاطع (`limit`, `offset`) |
| GET | `/api/documents/{id}/raw` | الملف كما رُفع — لفتح الاستشهاد عند مصدره |
| POST | `/api/chat` | سؤال قاعدة المعرفة |
| POST | `/api/chat/stream` | نفس الإجابة، مسبوقة بأحداث المراحل (NDJSON) |
| GET | `/api/chat/stages` | أسماء المراحل التي يبلّغ عنها الخادم |
| GET | `/api/health` | حالة Ollama و Qdrant |
| GET | `/api/config` | الإعدادات النافذة |

## طبقة المعرفة (Knowledge Layer)

معرفة يعلّمها الموظفون، مفصولة تمامًا عن أدلة المستندات. لا شيء يصل إلى إجابة إلا
بحالة `ACTIVE`، والوصول إليها يمرّ بدورة اعتماد كاملة.

```
اقتراح ──> PENDING ──> IN_REVIEW ──> APPROVED ──> ACTIVE ──> [يصل الإجابات]
                │                                    │
                └──> REJECTED                        └──> ARCHIVED ──> [يتوقف فورًا]
```

**الاستثناء الوحيد:** تفضيل شخصي بنطاق `USER` يُفعَّل فورًا لصاحبه وحده — لأنه يمسّ
الصياغة لا المضمون، ولا يراه أحد غيره. الحقيقة الشخصية تبقى تحتاج اعتمادًا.

| النوع | ما يفعله |
|---|---|
| `fact`, `correction`, `procedure`, `terminology` | يُعرض كدليل موسوم بأنه «معرفة معتمدة»، ويُقارن بالمستندات |
| `rule`, `preference` | موجّه لطريقة الإجابة — لا يُعرض كدليل ولا يُقتبس كحقيقة |

**الأولوية:** دليل مستندي ◀ معرفة معتمدة ◀ قاعدة معتمدة ◀ تفضيل. وعند اختلاف معرفة
معتمدة مع مستند، تُعرض القيمتان بمصدريهما ولا يُرجَّح أحدهما.

`ENABLE_KNOWLEDGE_LAYER=false` يعزل الذراع كليًّا: لا استعلام ولا تغيير في المُوجِّه.

| Method | Endpoint | الوظيفة |
|---|---|---|
| GET | `/api/knowledge` | قائمة مُفلترة (type, scope, status, q, tag, mine) |
| POST | `/api/knowledge` | تعليم عنصر جديد — يبدأ `PENDING` |
| GET | `/api/knowledge/stats` | إحصاءات مجمّعة في قاعدة البيانات |
| GET | `/api/knowledge/{id}` | العنصر مع نسخه وسجل قراراته واستخداماته |
| PATCH | `/api/knowledge/{id}` | تعديل — يُنشئ نسخة ويسحب الاعتماد |
| POST | `/api/knowledge/{id}/{submit,review,approve,activate,reject,archive,restore}` | انتقالات الحالة |

### حسم التعارض وأولوية المصادر

التعارض يُرصد بين ثلاثة أزواج: مستند/مستند، معرفة/معرفة، معرفة/مستند. «نفس الموضوع»
يُحدَّد بكلمتين مشتركتين على الأقل، و«الاختلاف» بقيمة تنفرد بها كل جهة عن الأخرى.

**الترتيب مفروض بمقارنة بيانات وصفية، لا بتعليمة للنموذج:**

| الاختبار | يحسم حين |
|---|---|
| `status` | أحد المصدرين `draft` أو `superseded` |
| `authority_rank` | رتبة معلنة مختلفة |
| `document_over_knowledge` | مستند مقابل معرفة معتمدة — المستند يسود |
| `knowledge_scope` | بين عنصري معرفة: الأوسع نطاقًا يسود |
| `effective_date` | تاريخا سريان معلنان ومختلفان |
| `version` | إصداران رقميان قابلان للمقارنة |

**إن لم ينطبق أيٌّ منها: لا يُحسم.** تُعرض القيمتان بمصدريهما، ويُسجَّل الأساس
`unresolved` في أثر الإجابة. لا تخمين، ولا ترجيح صامت من النموذج.

فضاءان متجهيان منفصلان: `alyafour_knowledge_base` للمستندات و`alyafour_knowledge_items`
للمعرفة. جملة يكتبها موظف لا تنافس بندًا تعاقديًّا على مقعد في ترتيب واحد.

**حدّان معروفان:** الأرقام المكتوبة بالحروف لا تُقارَن (اختبار صريح يوثّق ذلك)، والمعرفة
المعتمدة لا تُجيب وحدها — إن لم يُرجع مُسترجِع المستندات مرشّحًا، يُرفض السؤال قبل أن
يعمل ذراع المعرفة.

### لماذا هذه الإجابة

`GET /api/chat/answers/{answer_id}/why` يُرجع أربع قوائم منفصلة: أدلة مستندية، معرفة
معتمدة، سياسات مطبَّقة، وتعارضات مع أساس حسم كل واحد. **لا تُطلب سلسلة استدلال ولا
تُحفظ** — تفسيرٌ مبنيّ على مصادر يمكن التحقق منه، وسردٌ للتفكير لا يمكن إلا تصديقه.

## واجهة الويب

تطبيق صفحة واحدة في [`web/`](web/) — React + TypeScript + Vite. يقدّمه FastAPI نفسه في
الإنتاج، فلا يعبر المتصفح أصلًا مختلفًا ولا يحتاج CORS.

```bash
# تطوير: خادم Vite على 5173 يمرّر /api إلى 8000
cd web && npm install && npm run dev

# إنتاج: يُبنى إلى web/dist، ويلتقطه التطبيق تلقائيًا عند الإقلاع
cd web && npm run build
python run.py            # http://127.0.0.1:8000

# أو الحزمة كاملة في حاوية واحدة
docker compose --profile app up -d --build
```

التحقق: `npm run build` و`npm run lint` و`npm test` داخل `web/`.

`POST /api/chat/stream` لا يبثّ نص الإجابة — المسار يتحقق من الإجابة قبل إرجاعها فلا يوجد
نص جزئي يُرسل. ما يبثّه هو المرحلة التي بلغها الطلب فعلًا، مأخوذة من سجلّات التنفيذ نفسها.

## Metadata

**المستند:** `document_id, filename, title, category, source, version, date, language, uploaded_at, status, chunk_count`

**الـChunk:** `document_id, filename, chunk_id, chunk_index, heading, section, page_or_section, content, source, version, category, language`

ترتيب أولوية الـMetadata: القيم المُدخلة عند الرفع ← YAML Front Matter ← المستخرجة من المحتوى.

```markdown
---
title: إجراءات المطالبات
category: FIDIC
source: FIDIC Red Book 1999
version: 2.1
date: 2026-01-15
---
```

## الإجابات المقيّدة

النظام يجيب من المقاطع المسترجعة فقط. إذا لم تتضمنها قاعدة المعرفة يرد:

```
لا توجد معلومات كافية في قاعدة المعرفة للإجابة عن هذا السؤال.
```

كل إجابة مصحوبة بقائمة المصادر (اسم الملف، القسم، الإصدار، درجة التطابق، مقتطف).

## الإعدادات

كل القيم في `.env` — لا شيء مثبّت في الكود. أهمها:

| المتغير | الافتراضي | الأثر |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3:latest` | نموذج الإجابة |
| `EMBEDDING_MODEL` | `bge-m3:latest` | نموذج الـEmbeddings |
| `OLLAMA_KEEP_ALIVE` | `30m` | يمنع إعادة تحميل النموذج بين الأسئلة |
| `QDRANT_COLLECTION` | `alyafour_knowledge_base` | اسم المجموعة |
| `TOP_K` / `WIDE_TOP_K` | `8` / `14` | عدد المقاطع للسؤال المركّز / الموسّع |
| `CANDIDATE_POOL` | `30` | عدد المرشحين من كل مسار قبل إعادة الترتيب |
| `SCORE_THRESHOLD` | `0.35` | عتبة اعتبار السؤال مُغطّى دلاليًا |
| `VECTOR_SCORE_FLOOR` | `0.25` | أدنى تشابه يدخل قائمة المرشحين |
| `MAX_CONTEXT_CHARS` / `NARROW_CONTEXT_CHARS` | `12000` / `7000` | ميزانية السياق — **المحرك الأول للزمن** |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1400` / `200` | حجم المقطع والتداخل |
| `ENABLE_KEYWORD_SEARCH` / `ENABLE_ENTITY_RETRIEVAL` / `ENABLE_EXPANSION` | `true` | إيقاف أي مسار استرجاع لعزل أثره |
| `ANSWER_RETRY_ON_INCOMPLETE` | `false` | إعادة توليد أوسع عند رصد نقص (تكلفة توليد ثانٍ) |

### ملاحظة أداء مقيسة على هذا الجهاز

كرت MX250 بذاكرة 2 GiB لا يستوعب نموذج 8B، فالتوليد يجري على المعالج:

| القياس | القيمة |
|---|---|
| سرعة معالجة السياق | ~58 توكن/ثانية |
| متوسط العربية | 2.24 حرف/توكن |
| **كلفة كل 1000 حرف سياق** | **~7.7 ثانية** |

لذلك السؤال المركّز يأخذ ميزانية 7000 حرف، والسؤال الموسّع فقط يدفع 12000.
أي تعديل على `MAX_CONTEXT_CHARS` يترجم مباشرة إلى زمن.

> تغيير `EMBEDDING_MODEL` إلى نموذج بأبعاد مختلفة يستلزم تغيير `QDRANT_COLLECTION`
> أو حذف المجموعة الحالية، ثم إعادة فهرسة المستندات.

## الاختبار

```bash
# بلا خدمات خارجية
python tests/test_parsing_and_chunking.py
python tests/test_intelligence.py

# يتطلب تشغيل التطبيق و Qdrant و Ollama
python tests/test_end_to_end.py

# تقييم كامل (38 سؤالًا) ومقارنة قبل/بعد
python tests/eval/run_eval.py --label after
python tests/eval/run_eval.py --compare baseline after
```

مجموعة التقييم في [tests/eval/dataset.py](tests/eval/dataset.py) وتغطي: أسئلة واقعية،
رقمية، تواريخ، كيانات، متعددة الأقسام، مقارنات، تسلسل زمني، عبر مستندات، وأسئلة
خارج قاعدة المعرفة. المقاييس: اكتمال الإجابة، دقة الاستشهاد، نسبة الاختلاق، دقة
الرفض، والزمن.

## التوسع لأنواع ملفات أخرى (Phase 2)

أنشئ صنفًا يرث `DocumentParser` وسجّله في `Container`:

```python
class PdfParser(DocumentParser):
    name = "pdf"
    supported_extensions = (".pdf",)

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        ...
```

```python
self.parser_registry = ParserRegistry([MarkdownParser(...), PdfParser()])
```

ثم أضف الامتداد إلى `ALLOWED_EXTENSIONS`. لا يتغير أي شيء آخر في النظام.
