# ERP Integration — Phase 1 Operations

مرجع تشغيلي لجانب RAG. العقد المرجعي: `FINAL_ERP_RAG_INTEGRATION_CONTRACT.md`.

> **الحالة:** الكود جاهز، والواجهة **معطّلة افتراضيًا**. لا شيء يتغيّر في التشغيل الحالي
> حتى يضبط المشغّل الإعدادات أدناه صراحةً. لم يُعدَّل ملف `.env` على هذا الجهاز.

---

## 1. ما يجب إضافته إلى `.env`

```ini
# --- ERP integration (Phase 1) ---
INTEGRATION_ENABLED=true
INTEGRATION_MASTER_KEY=<من الأمر أدناه — 32 حرفًا على الأقل>

# يجب أن تبقى true في الإنتاج. اضبطها false فقط على جهاز اختبار محلي.
INTEGRATION_REQUIRE_HTTPS=true

# اختيارية — هذه هي القيم الافتراضية
INTEGRATION_TIMESTAMP_SKEW_SECONDS=300
INTEGRATION_NONCE_TTL_SECONDS=600
INTEGRATION_ANSWER_BUDGET_SECONDS=90
INTEGRATION_RATE_LIMIT_PER_MINUTE=6
INTEGRATION_RATE_LIMIT_BURST=10
INTEGRATION_MAX_CONCURRENCY=2
INTEGRATION_QUEUE_DEPTH=4
INTEGRATION_DAILY_QUOTA=500
INTEGRATION_IDEMPOTENCY_TTL_HOURS=24
INTEGRATION_MAX_BODY_BYTES=65536
```

**تحذيران:**

1. `INTEGRATION_MASTER_KEY` هو أصل كل اعتماد تكامل. فقدانه يُبطل كل المفاتيح، وحيازته
   تعادل حيازتها جميعًا. يُحفظ في مدير كلمات مرور، ولا يُكتب في مستودع.
2. `INTEGRATION_REQUIRE_HTTPS=false` يعني أن أسعار الموردين وأرقام أوامر الشراء ستعبر
   الشبكة بالنص الصريح. التوقيع يثبت أن الجسم لم يُعدَّل — **ولا يخفيه**.

---

## 2. الأوامر

```bash
# مفتاح رئيسي جديد (لا يُخزَّن بهذا الأمر — انسخه بنفسك)
python -m app.cli integration-master-key

# اعتماد لعميل ومستأجر
python -m app.cli integration-create "ERP Production" "alyafour-main"

# القائمة
python -m app.cli integration-list

# تدوير السر (القديم يبقى صالحًا 7 أيام)
python -m app.cli integration-rotate <key_id>

# إبطال فوري
python -m app.cli integration-revoke <key_id>
```

`integration-create` يطبع **السر مرة واحدة**. قاعدة البيانات لا تحتفظ بنسخة منه، بل
ببصمة فقط؛ اشتقاقه ثانيةً يحتاج المفتاح الرئيسي.

---

## 3. ما يرسله ERP

```http
POST /api/v1/integrations/erp/query
Content-Type: application/json; charset=utf-8
Authorization: Bearer rag_sk_<key_id>          ← المعرّف فقط، لا السر
X-RAG-Key-Id: <key_id>
X-RAG-Timestamp: <unix seconds>
X-RAG-Nonce: <عشوائي، ≤64 حرفًا، فريد لكل طلب>
X-RAG-Signature: <hex>
Idempotency-Key: <≤128 حرفًا>
```

**المادة الموقَّعة** — مفصولة بسطر جديد، بهذا الترتيب حرفيًا:

```
POST
/api/v1/integrations/erp/query
<sha256_hex(الجسم كما أُرسل بالبايت)>
<X-RAG-Timestamp>
<X-RAG-Nonce>
```

```
X-RAG-Signature = hex( HMAC-SHA256( key = <secret>, message = <المادة أعلاه> ) )
```

**التنفيذ المرجعي:** `tests/integration_client.py` — يوضّح أن الجسم يُسلسَل مرة واحدة،
وأن البايتات نفسها هي ما يُوقَّع ويُرسَل. إعادة تسلسل الكائن قبل الإرسال تكسر التوقيع.

---

## 4. تشغيل الاختبارات

```bash
# خادم اختبار (لاحظ REQUIRE_HTTPS=false لأن الاختبار محلي على HTTP)
INTEGRATION_ENABLED=true \
INTEGRATION_MASTER_KEY=<...> \
INTEGRATION_REQUIRE_HTTPS=false \
WEB_SEARCH_ENABLED=true \
APP_PORT=8080 python run.py

# قبول Phase 1
RAG_BASE_URL=http://localhost:8080 \
ERP_RAG_KEY=rag_sk_<key_id> \
ERP_RAG_SECRET=<secret> \
ERP_RAG_TENANT=alyafour-main \
python tests/test_erp_integration_phase1.py

# إشارة البيانات الحية (بلا خادم)
python tests/test_live_data_signal.py

# بوابة الانحدار — تُشغَّل بإعدادات الإنتاج (WEB_SEARCH_ENABLED=false)
RAG_BASE_URL=http://localhost:8080 python tests/eval/run_eval.py --label <اسم>
python tests/eval/run_eval.py --compare final_v4 <اسم>

# تجميد النواة
python tests/rag_core_checksums.py --compare erp_phase1_after
```

**ملاحظة على بوابة الانحدار:** لا تُشغَّل و`WEB_SEARCH_ENABLED=true`. مع تفعيل الويب
تُجاب الأسئلة الأربعة خارج القاعدة (O1–O4) من الإنترنت بدل رفضها، فينهار
`refusal_accuracy` إلى صفر — وهو سلوك بحث الويب المصمَّم، لا انحدار.

---

## 5. ما لا يشمله Phase 1

| غير مُنفَّذ | المرحلة |
|---|---|
| الإجابة من `context.erp_facts` | 6 — يُرفض اليوم بـ400 `ERP_FACTS_NOT_SUPPORTED_IN_PHASE_1` |
| نطاقا `team` و`user` للمعرفة | 3+ |
| نطاق `department` | بعد إصلاح ثغرة `knowledge_index.py` |
| مسار `/trace` للتدقيق من ERP | 4 |
| مرشّحات معرفة الويب | 5 |
| `tenant_id` على المستندات (عزل منطقي) | مستقبلي — العزل اليوم بنسخة لكل مستأجر |

---

## 6. حدود معروفة، مذكورة صراحةً

1. **حدّا المعدل والتوازي محليان للعملية.** النشر الحالي عملية uvicorn واحدة، وهو
   صحيح لها. إضافة عامل ثانٍ تعني أن كل عامل يفرض الحد كاملًا وحده. الحصة اليومية
   وحدها محسوبة من قاعدة البيانات، فتصمد مهما تعدّدت العمليات.
2. **مهلة 90 ثانية لا تُلغي التوليد.** لا يمكن سحب طلب من النموذج بعد إرساله، فتبقى
   خانة التوازي محجوزة حتى ينتهي. هذا مقصود: تحريرها فورًا يعني بدء طلب ثانٍ على نموذج
   ما زال مشغولًا بالأول.
3. **`ollama_timeout` ما زال 600 ثانية** على مستوى التطبيق. مسار التكامل محكوم بـ90
   ثانية عبر `INTEGRATION_ANSWER_BUDGET_SECONDS`، لكن الخيط الخلفي قد يستمر حتى 600.
4. **التحقق من انعدام النداءات الخارجية تمّ بالسلوك لا بالتقاط الحزم.** شُغّلت مجموعة
   القبول كاملة و`WEB_SEARCH_ENABLED=true`، وسجل الخادم يحتوي **صفر** أسطر
   `Web fallback:` — والنظام يسجّل سطرًا عند كل محاولة بحث بما فيها الفاشلة. التقاط
   حركة الشبكة يبقى خطوة تشغيلية على المشغّل قبل الإنتاج.
5. **مزامنة الساعات إلزامية.** انحراف يتجاوز 300 ثانية بين الجهازين يظهر كفشل مصادقة
   متقطع بالرمز `CLOCK_SKEW`، وهو من أصعب الأعطال تشخيصًا بلا هذا الرمز.
