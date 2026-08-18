# نظام إدارة تكاليف المشاريع — نسخة Django

إعادة بناء كاملة للنظام باستخدام **Django + HTMX + DaisyUI**، مع نقل كل البيانات من النظام السابق.

---

## التشغيل السريع

```bash
cd factory-django
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

ثم افتح: **http://localhost:8000**

| الحساب | كلمة المرور | الصلاحية |
|---|---|---|
| `admin` | `admin123` | مدير |
| `accountant1` | (كما كانت في النظام السابق) | محاسب |
| `employee1` | (كما كانت في النظام السابق) | موظف |

> كلمات المرور القديمة نُقلت كما هي وتعمل بدون تغيير.

---

## التشغيل عبر Docker مع Supabase

### 1) الحصول على رابط الاتصال من Supabase

من لوحة تحكم Supabase: **Project Settings ← Database ← Connection string ← URI**

اختر **Session pooler** (المنفذ `5432`):

```
postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

> **مهم:** الاتصال المباشر (`db.PROJECT_REF.supabase.co`) يعمل عبر IPv6 فقط في المشاريع
> الجديدة، وقد لا يصل من شبكتك. مجمّع الجلسات (Session pooler) يعمل عبر IPv4 ويصلح
> للترحيلات والاستيراد.
>
> مجمّع المعاملات (المنفذ `6543`) مناسب لعدد اتصالات أكبر، والنظام يضبط نفسه له
> تلقائياً — لكن نفّذ الترحيلات والاستيراد عبر المنفذ `5432`.

### 2) إعداد ملف البيئة

```bash
cp .env.example .env
```

ثم عدّل `.env` وضع فيه رابط Supabase ومفتاحاً سرياً جديداً:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 3) التشغيل

```bash
docker compose up -d --build
```

الترحيلات تُطبَّق تلقائياً عند الإقلاع. افتح: **http://localhost:8000**

### 4) نقل بيانات النظام القديم (مرة واحدة)

أوقف النظام القديم أولاً، ثم:

```bash
docker compose --profile tools run --rm import
```

يقرأ من `../factory-projects/server` افتراضياً. لمسار آخر:

```bash
LEGACY_DIR=/path/to/server docker compose --profile tools run --rm import
```

> **مهم:** يجب ربط **المجلد كاملاً** وليس ملف `.db` وحده — قاعدة البيانات القديمة
> تعمل بوضع WAL وبياناتها موزّعة بين `factory_management.db` وملف `-wal` بجانبه.
> الاستيراد يتحقق من ذلك ويتوقف برسالة واضحة إن كان الملف ناقصاً.
> الملف الأصلي يُنسخ إلى مجلد مؤقت ولا يُعدَّل إطلاقاً.

### أوامر مفيدة

```bash
docker compose logs -f web          # متابعة السجلات
docker compose restart web          # إعادة التشغيل
docker compose down                 # إيقاف
docker compose up -d --build        # إعادة البناء بعد تعديل الكود

# أمر Django داخل الحاوية
docker compose exec web python manage.py <command>
```

### ملاحظات

- الصورة تعمل بمستخدم غير جذري (`factory`, uid 10001).
- الملفات الثابتة تُخدَم عبر WhiteNoise — لا حاجة لـ Nginx للتشغيل الداخلي.
- بدون `DATABASE_URL` يعود النظام تلقائياً إلى SQLite داخل مجلد `/app/data`
  (مخزَّن في volume) — مفيد للتجربة السريعة دون Supabase.
- عند التشغيل خلف HTTPS أضف في `.env`:
  `DJANGO_BEHIND_PROXY=1` و `DJANGO_SECURE_COOKIES=1` و `DJANGO_CSRF_TRUSTED=https://your-domain`

---

## أداة Supabase CLI

مثبّتة في `~/.local/bin/supabase` (الإصدار 2.114.0).

### الخطوتان المتبقيتان (نفّذهما بنفسك — تتطلبان بياناتك)

**1) تسجيل الدخول** — يفتح المتصفح لتفويض الحساب:

```bash
supabase login
```

بديل بدون متصفح: أنشئ رمزاً من https://supabase.com/dashboard/account/tokens ثم:

```bash
export SUPABASE_ACCESS_TOKEN=رمز-الوصول
```

(يفضَّل متغير البيئة على `supabase login --token` حتى لا يُحفظ الرمز في سجل الأوامر.)

**2) ربط المشروع** — يطلب كلمة مرور قاعدة البيانات:

```bash
cd factory-django
supabase link --project-ref PROJECT_REF
```

`PROJECT_REF` هو المعرّف في رابط لوحة التحكم:
`https://supabase.com/dashboard/project/` **`PROJECT_REF`**

للتأكد من نجاح الربط:

```bash
supabase projects list      # المشروع المرتبط مُعلَّم في عمود LINKED
```

### الحصول على رابط الاتصال لـ Django

بعد الربط، من لوحة التحكم: **Connect ← Session pooler**، وانسخ الرابط إلى
`DATABASE_URL` في ملف `.env`.

### تحذير مهم

**مخطط قاعدة البيانات تديره ترحيلات Django** (`core/migrations/`) وليس
`supabase/migrations/`. لا تنفّذ هذه الأوامر على المشروع المرتبط:

```
supabase db reset --linked     ← يمحو كل البيانات
supabase db push               ← يفرض مخططاً فارغاً
```

لتعديل المخطط: عدّل `core/models.py` ثم:

```bash
.venv/bin/python manage.py makemigrations
.venv/bin/python manage.py migrate
```

### أوامر مفيدة بعد الربط

```bash
supabase projects list                  # قائمة المشاريع
supabase db dump -f backup.sql --linked # نسخة احتياطية من المخطط
supabase db dump -f data.sql --linked --data-only   # نسخة من البيانات
supabase inspect db table-sizes --linked            # أحجام الجداول
```

---

## التثبيت من الصفر (بدون Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py import_legacy --source /path/to/factory_management.db
```

### بناء ملف التنسيق (عند تعديل القوالب فقط)

```bash
npm install
npx tailwindcss -i ./assets/app.css -o ./core/static/css/app.css --minify
```

ملف `core/static/css/app.css` مبني مسبقاً — لا حاجة لـ Node إلا عند تعديل التصميم.

---

## الصلاحيات

تُفرَض على الخادم في كل مسار (`core/permissions.py`):

| الدور | الصلاحيات |
|---|---|
| **مدير** `admin` | كل شيء، بما فيه إدارة المستخدمين |
| **محاسب** `accountant` | تعديل المشاريع والموظفين والفواتير والدفعات — بدون إدارة المستخدمين |
| **موظف** `employee` | صفحة تسجيل ساعات العمل فقط |

الوصول بدون تسجيل دخول يُحوَّل دائماً إلى صفحة الدخول.

---

## نقل البيانات من النظام القديم

قاعدة بيانات النظام القديم **حيّة وتتغيّر** طالما أنه يعمل. قبل التحويل النهائي
أوقف النظام القديم تماماً ثم أعد الاستيراد.

**عبر Docker:**

```bash
docker compose --profile tools run --rm import
```

**بدون Docker:**

```bash
.venv/bin/python manage.py import_legacy \
    --source ../factory-projects/server/factory_management.db --flush
```

`--flush` تحذف بيانات Django الحالية قبل الاستيراد. الملف القديم يُنسخ إلى مجلد
مؤقت للقراءة ولا يُعدَّل إطلاقاً.

## البنية

```
factory-django/
├── config/           إعدادات المشروع
├── core/
│   ├── models.py     النماذج (مشاريع، موظفون، ساعات، فواتير، دفعات، مصروفات)
│   ├── services.py   منطق حساب التكاليف وإحصاءات اللوحة
│   ├── views.py      المسارات
│   ├── permissions.py الصلاحيات
│   ├── forms.py      نماذج الإدخال والتحقق
│   ├── reports.py    تقارير PDF عبر WeasyPrint
│   ├── templates/    قوالب RTL بـ DaisyUI
│   └── management/commands/import_legacy.py
├── assets/app.css    مصدر التنسيق (Tailwind + DaisyUI)
└── requirements.txt
```

---

## معادلات حساب التكلفة

منقولة حرفياً من النظام السابق ومتحقَّق من تطابقها:

```
تكلفة الموظفين = Σ (ساعات عادية × أجر الساعة + ساعات إضافية × الأجر الإضافي)
                  الأجر الإضافي = المُسجَّل، وإن لم يوجد فـ 1.5 × أجر الساعة

التكلفة الكلية  = تكلفة الموظفين + الفواتير + المصروفات
المتبقي        = الميزانية − التكلفة الكلية
نسبة الاستخدام  = التكلفة الكلية ÷ الميزانية × 100

الحالة: أكثر من 100% = تجاوز الميزانية · أكثر من 80% = تحذير · غير ذلك = ضمن الميزانية
```

عند إضافة موظف تُحسب الأجور تلقائياً (ويمكن تعديلها يدوياً):

```
أجر الساعة   = (الراتب الأساسي + 1000) ÷ 26 ÷ 8
الأجر الإضافي = الراتب الأساسي ÷ 30 ÷ 9 × 1.5
```

---

## التشغيل على شبكة المصنع

```bash
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

ثم من أي جهاز على الشبكة: `http://<عنوان-الخادم>:8000`

للتشغيل الحقيقي (وليس التطوير):

```bash
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="مفتاح-سري-طويل-وعشوائي"
export DJANGO_ALLOWED_HOSTS="192.168.1.50,factory.local"
.venv/bin/python manage.py collectstatic --noinput
```

عند التشغيل خلف HTTPS فعّل أيضاً: `export DJANGO_SECURE_COOKIES=1`

---

## تقارير PDF

تُنتَج عبر WeasyPrint (بدون متصفح) من صفحة أي مشروع → **تحميل التقرير PDF**.
يعتمد على خط `Noto Kufi Arabic` المثبَّت على النظام. لتثبيته على خادم جديد:

```bash
sudo apt install fonts-noto-core libpango-1.0-0 libpangoft2-1.0-0
```
