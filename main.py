from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import random
import uuid

app = FastAPI(title="Beheira Security Monitor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store
alerts_store: list[dict] = []
last_scrape = datetime.utcnow().isoformat()

LOCATIONS = ["البحيرة", "دمنهور", "كفر الدوار", "إيتاي البارود", "كوم حمادة", "رشيد", "إدكو", "أبو حمص", "شبراخيت", "الدلنجات"]
TYPES = ["استغاثة", "حادث", "سرقة", "مشاجرة", "حريق", "خبر"]
PRIORITY_MAP = {"استغاثة": "high", "حريق": "high", "حادث": "medium", "سرقة": "medium", "مشاجرة": "medium", "خبر": "low"}

TEXTS = [
    "تم رصد حالة استغاثة من مواطن في المنطقة السكنية بالقرب من مدرسة الثانوية العامة",
    "حادث مروري خطير على الطريق الرئيسي بالقرب من المدخل الشرقي للمدينة",
    "بلاغ عن محاولة سرقة في منطقة السوق القديم من شهود عيان",
    "مشاجرة كبيرة بين مجموعة أشخاص أمام المقهى المركزي في وسط البلد",
    "نشوب حريق كبير في مخزن مهجور بالمنطقة الصناعية",
    "خبر عاجل: افتتاح مشروع جديد في المحافظة لتطوير البنية التحتية",
    "بلاغ عن صوت انفجار بالقرب من محطة القطار الرئيسية",
    "حالة اشتباه في سرقة سيارة بمنطقة الكورنيش الغربي",
]

def generate_alerts():
    global alerts_store, last_scrape
    alerts_store = []
    for i in range(15):
        alert_type = random.choice(TYPES)
        location = random.choice(LOCATIONS)
        minutes_ago = random.randint(0, 120)
        ts = datetime.utcnow() - timedelta(minutes=minutes_ago)
        alerts_store.append({
            "id": f"alert-{uuid.uuid4().hex[:8]}",
            "location": location,
            "type": alert_type,
            "priority": "high" if location == "البحيرة" else PRIORITY_MAP.get(alert_type, "low"),
            "status": random.choice(["confirmed", "normal"]),
            "text": random.choice(TEXTS),
            "full_text": random.choice(TEXTS) + " تم إبلاغ الشرطة وتوجهت دورية أمنية للمكان. الموقف تحت السيطرة.",
            "post_link": f"https://facebook.com/post/{uuid.uuid4().hex[:8]}",
            "timestamp": ts.isoformat(),
            "is_new": i < 3,
        })
    alerts_store.sort(key=lambda a: a["timestamp"], reverse=True)
    last_scrape = datetime.utcnow().isoformat()

generate_alerts()

# --- Auth ---
ADMIN_USER = "admin"
ADMIN_PASS = "admin"
tokens: set[str] = set()

@app.post("/login")
def login(body: dict):
    if body.get("username") == ADMIN_USER and body.get("password") == ADMIN_PASS:
        token = f"token-{uuid.uuid4().hex}"
        tokens.add(token)
        return {"token": token}
    raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

# --- Endpoints ---
@app.get("/status")
def status():
    return {"status": "running", "last_scrape": last_scrape}

@app.get("/alerts")
def get_alerts():
    return alerts_store

@app.get("/alerts/latest")
def get_latest_alert():
    return alerts_store[0] if alerts_store else None

@app.get("/google-alerts")
def get_google_alerts():
    return [
        {"id": "ga-1", "title": "تطورات أمنية في محافظة البحيرة", "source": "Google Alerts", "timestamp": datetime.utcnow().isoformat(), "url": "#"},
        {"id": "ga-2", "title": "حادث مروري على طريق دمنهور الرئيسي", "source": "Google Alerts", "timestamp": (datetime.utcnow() - timedelta(hours=1)).isoformat(), "url": "#"},
        {"id": "ga-3", "title": "حملة أمنية موسعة بكفر الدوار", "source": "Google Alerts", "timestamp": (datetime.utcnow() - timedelta(hours=2)).isoformat(), "url": "#"},
    ]

@app.get("/news")
def get_news():
    return [
        {"id": "n-1", "title": "محافظ البحيرة يتابع الحالة الأمنية", "source": "الأهرام", "timestamp": datetime.utcnow().isoformat(), "summary": "عقد محافظ البحيرة اجتماعاً أمنياً موسعاً", "url": "#"},
        {"id": "n-2", "title": "ضبط تشكيل عصابي في دمنهور", "source": "اليوم السابع", "timestamp": (datetime.utcnow() - timedelta(minutes=30)).isoformat(), "summary": "تمكنت أجهزة الأمن من ضبط تشكيل عصابي", "url": "#"},
        {"id": "n-3", "title": "إصابة 5 أشخاص في حادث على طريق رشيد", "source": "المصري اليوم", "timestamp": (datetime.utcnow() - timedelta(hours=1, minutes=30)).isoformat(), "summary": "أصيب 5 أشخاص إثر حادث تصادم", "url": "#"},
    ]

@app.get("/account/status")
def account_status():
    return {"status": "active", "is_logged_in": True, "is_running": True}

@app.get("/open-browser")
def open_browser():
    return {"success": True, "message": "تم فتح المتصفح بنجاح"}

@app.post("/account/login")
def fb_login(email: str = Query(...), password: str = Query(...)):
    return {"success": True, "message": "تم تسجيل الدخول بنجاح"}

@app.get("/")
def root():
    return {"message": "Beheira Security Monitor API", "status": "running"}
