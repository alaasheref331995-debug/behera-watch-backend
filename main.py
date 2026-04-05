import asyncio
import re
import os
import time
import requests
import urllib.parse
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

# ====================================================
#  إعدادات
# ====================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8675425131:AAFis74NgxGC0KT96XB14qTBT_AL3JeH0to")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5134151930")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCRVmQQNt_33__Ll5QjFSItC63rpMmf-BQ")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin")

SEARCH_TERMS = [
    "البحيرة", "الدلنجات", "حوش عيسي", "وادي النطرون",
    "ابو المطامير", "كوم حمادة", "رشيد", "المحمودية",
    "الرحمانية", "شبراخيت", "دمنهور", "النوبارية",
    "ايتاي البارود", "كفرالدوار", "ابو حمص", "ادكو", "بدر",
]

ALERT_KEYWORDS = [
    "سلاح", "مشاجرة", "اغاثة", "استغاثة", "استغاثه",
    "الحقوني", "الحقونا", "ضرب", "سرقة", "بلطجة",
    "بلطجية", "ضربني", "ضربوني", "اتهجمو عليا",
    "بلطجي", "البلطجي", "البلطجة", "البلطجية",
    "غرق", "غريق", "حادث", "حادثة", "تصادم", "مصادمة",
    "حسبي الله", "حسبنا الله", "يسرق", "بيسرق",
    "تسرق", "بتسرق", "مدير الامن", "مديرية الامن",
    "النائب العام", "نداء", "انهيار", "شغب", "عاجل",
    "وزير الداخلية", "تحرش", "التحرش",
]

# ====================================================
#  حالة النظام
# ====================================================
state = {
    "status": "stopped",
    "last_scrape": None,
    "active_tabs": 0,
    "total_alerts": 0,
    "fb_email": None,
    "fb_password": None,
    "is_logged_in": False,
    "is_running": False,
    "browser": None,
    "context": None,
    "playwright": None,
    "login_page": None,        # صفحة تسجيل الدخول - تفضل مفتوحة
    "login_ready": False,      # هل المتصفح مفتوح ومستني التأكيد
    "monitoring_tasks": [],
}

alerts_store = []
tokens = {}

# ====================================================
#  مساعدات
# ====================================================
def log(term, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}][{term}] {msg}")

def clean_arabic_text(text):
    if not text: return ""
    text = text.strip().lower()
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r'[^\w\s]', '', text)
    return text

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"❌ خطأ تيليجرام: {e}")

def classify_alert(text, keywords_found):
    text_lower = clean_arabic_text(text)
    high_words = ["سلاح", "استغاثة", "استغاثه", "اغاثة", "الحقوني", "الحقونا", "غرق", "غريق", "انهيار"]
    medium_words = ["حادث", "حادثة", "تصادم", "سرقة", "مشاجرة", "ضرب", "بلطجة", "تحرش"]
    
    for kw in keywords_found:
        if clean_arabic_text(kw) in [clean_arabic_text(w) for w in high_words]:
            return "high", "استغاثة"
        if clean_arabic_text(kw) in [clean_arabic_text(w) for w in medium_words]:
            return "medium", "حادث"
    return "low", "خبر"

def verify_token(token: str = None):
    if not token:
        raise HTTPException(401, "غير مصرح")
    clean = token.replace("Bearer ", "")
    if clean not in tokens:
        raise HTTPException(401, "رمز غير صالح")
    return clean

# ====================================================
#  Playwright - إغلاق النوافذ المنبثقة
# ====================================================
async def close_popups(page):
    for selector in [
        "div[aria-label='Close']", "button[aria-label='Close']",
        "div[aria-label='إغلاق']", "button[aria-label='إغلاق']",
        "div[role='dialog'] button[type='button']",
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible():
                await btn.click()
                await asyncio.sleep(1)
                break
        except:
            pass

# ====================================================
#  تفعيل Most Recent
# ====================================================
async def activate_most_recent(page, term):
    try:
        await close_popups(page)
        await asyncio.sleep(3)

        # الضغط على "All"
        all_clicked = False
        for _ in range(3):
            try:
                for el in await page.get_by_text("All", exact=True).all():
                    if await el.is_visible():
                        await el.click()
                        log(term, "✅ ضغط All")
                        await asyncio.sleep(3)
                        all_clicked = True
                        break
            except:
                pass
            if all_clicked:
                break
            await asyncio.sleep(2)

        # تفعيل Recent Posts
        recent_activated = False
        for attempt in range(1, 10):
            xpaths = [
                "xpath=//*[contains(text(),'Recent Posts')]/following::input[@type='checkbox'][1]",
                "xpath=//*[contains(text(),'Recent posts')]/following::input[@type='checkbox'][1]",
                "xpath=//*[contains(text(),'Recent Posts')]/..//div[@role='switch']",
                "xpath=//*[contains(text(),'Recent posts')]/..//div[@role='switch']",
            ]
            for xp in xpaths:
                try:
                    el = page.locator(xp).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        log(term, f"✅ Recent Posts (محاولة {attempt})")
                        await asyncio.sleep(3)
                        recent_activated = True
                        break
                except:
                    pass
            if recent_activated:
                break

            try:
                toggles = await page.query_selector_all("input[type='checkbox'], div[role='switch']")
                for tog in toggles:
                    if await tog.is_visible():
                        await tog.click()
                        log(term, f"✅ toggle fallback ({attempt})")
                        await asyncio.sleep(3)
                        recent_activated = True
                        break
            except:
                pass
            if recent_activated:
                break

            if attempt % 3 == 0:
                try:
                    for el in await page.get_by_text("All", exact=True).all():
                        if await el.is_visible():
                            await el.click()
                            await asyncio.sleep(2)
                            break
                except:
                    pass
            await asyncio.sleep(4)

        return recent_activated
    except Exception as e:
        log(term, f"⚠️ خطأ activate_most_recent: {e}")
        return False

# ====================================================
#  مراقبة تاب واحد
# ====================================================
async def monitor_tab(context, search_term, start_delay=0):
    global alerts_store

    if start_delay > 0:
        await asyncio.sleep(start_delay)

    encoded = urllib.parse.quote(search_term)
    search_url = f"https://www.facebook.com/search/posts/?q={encoded}"
    seen_posts = set()

    state["active_tabs"] += 1

    while state["is_running"]:
        page = None
        try:
            page = await context.new_page()
            page.set_default_timeout(30000)

            await page.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,mp4,webm,mov,m4v}",
                lambda route: route.abort()
            )

            log(search_term, "🔍 فتح صفحة البحث...")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(7)

            log(search_term, "⚙️ تفعيل Most Recent...")
            await activate_most_recent(page, search_term)

            send_telegram(f"✅ <b>تاب '{search_term}' بدأ الرصد.</b>")
            log(search_term, "🟢 بدأ الرصد!")

            scroll_count = 0

            while state["is_running"]:
                if page.is_closed():
                    raise Exception("التاب اتقفل")

                await close_popups(page)
                posts = await page.query_selector_all("div[role='article'], div[data-ad-preview='message']")

                for post in posts:
                    try:
                        try:
                            see_more = await post.query_selector("text=See more")
                            if see_more:
                                await see_more.click()
                                await asyncio.sleep(0.3)
                        except:
                            pass

                        full_text = await post.inner_text()
                        if not full_text or len(full_text) < 15:
                            continue

                        searchable_text = clean_arabic_text(full_text)
                        post_id = hash(searchable_text[:120])

                        if post_id not in seen_posts:
                            found_alerts = [kw for kw in ALERT_KEYWORDS if clean_arabic_text(kw) in searchable_text]
                            if found_alerts:
                                link_el = await post.query_selector("a")
                                post_url = "رابط غير متاح"
                                if link_el:
                                    href = await link_el.get_attribute("href")
                                    if href:
                                        post_url = href.split('?')[0]
                                        if post_url.startswith("/"):
                                            post_url = "https://facebook.com" + post_url

                                priority, alert_type = classify_alert(full_text, found_alerts)

                                alert = {
                                    "id": f"alert-{int(time.time())}-{len(alerts_store)}",
                                    "location": search_term,
                                    "type": alert_type,
                                    "priority": priority,
                                    "status": "confirmed" if priority == "high" else "normal",
                                    "text": full_text[:200],
                                    "full_text": full_text[:1000],
                                    "post_link": post_url,
                                    "timestamp": datetime.now().isoformat(),
                                    "is_new": True,
                                    "keywords": found_alerts,
                                }
                                alerts_store.insert(0, alert)
                                state["total_alerts"] += 1
                                state["last_scrape"] = datetime.now().isoformat()

                                if len(alerts_store) > 50:
                                    alerts_store = alerts_store[:50]

                                msg = (
                                    f"🔔 <b>منشور مكتشف جديد</b>\n\n"
                                    f"🔎 <b>بحث:</b> {search_term}\n"
                                    f"🎯 <b>الكلمات:</b> {', '.join(found_alerts)}\n"
                                    f"⏰ <b>الوقت:</b> {datetime.now().strftime('%I:%M %p')}\n"
                                    f"🔗 <b>الرابط:</b> {post_url}\n"
                                    f"---------------------------\n"
                                    f"📝 <b>النص:</b>\n{full_text[:350]}..."
                                )
                                send_telegram(msg)
                                log(search_term, f"✅ رصد: {found_alerts}")
                            seen_posts.add(post_id)
                    except:
                        continue

                await page.mouse.wheel(0, 1000)
                scroll_count += 1
                await asyncio.sleep(3)

                if scroll_count >= 20:
                    log(search_term, "🔄 إعادة تحميل...")
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(7)
                    await activate_most_recent(page, search_term)
                    scroll_count = 0

                if len(seen_posts) > 1000:
                    seen_posts.clear()

        except Exception as e:
            log(search_term, f"⚠️ خطأ: {e}")
            try:
                if page and not page.is_closed():
                    await page.close()
            except:
                pass
            await asyncio.sleep(15)

    state["active_tabs"] -= 1

# ====================================================
#  فتح المتصفح وتسجيل الدخول (الخطوة الأولى)
# ====================================================
async def open_browser_and_login(email: str, password: str):
    """يفتح المتصفح ويسجل دخول Facebook - يفضل مفتوح مستني التأكيد"""
    try:
        # إغلاق أي جلسة قديمة
        await cleanup_browser()

        pw = await async_playwright().start()
        state["playwright"] = pw

        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ]
        )
        state["browser"] = browser

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ar-EG",
            timezone_id="Africa/Cairo",
        )
        state["context"] = context

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ar', 'en-US'] });
            window.chrome = { runtime: {} };
        """)

        # فتح صفحة تسجيل الدخول
        login_page = await context.new_page()
        state["login_page"] = login_page

        await login_page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # إدخال بيانات الدخول تلقائياً
        try:
            await login_page.fill("input[name='email']", email, timeout=10000)
            await asyncio.sleep(1)
            await login_page.fill("input[name='pass']", password, timeout=10000)
            await asyncio.sleep(1)
            await login_page.click("button[name='login']", timeout=10000)
            await asyncio.sleep(5)
            log("LOGIN", f"✅ تم إدخال البيانات والضغط على تسجيل الدخول")
        except Exception as e:
            log("LOGIN", f"⚠️ خطأ في إدخال البيانات: {e}")

        state["fb_email"] = email
        state["login_ready"] = True
        state["status"] = "waiting_confirm"

        log("LOGIN", "⏳ المتصفح مفتوح ومستني التأكيد من المستخدم...")

    except Exception as e:
        log("LOGIN", f"❌ خطأ: {e}")
        state["login_ready"] = False
        raise

# ====================================================
#  بدء المراقبة (الخطوة الثانية - بعد التأكيد)
# ====================================================
async def start_monitoring():
    """بعد تأكيد المستخدم - يقفل صفحة الدخول ويبدأ التابات"""
    context = state.get("context")
    if not context:
        raise Exception("المتصفح مش مفتوح")

    # إغلاق صفحة تسجيل الدخول
    login_page = state.get("login_page")
    if login_page and not login_page.is_closed():
        try:
            await login_page.close()
        except:
            pass
    state["login_page"] = None

    state["is_logged_in"] = True
    state["is_running"] = True
    state["status"] = "running"
    state["login_ready"] = False

    send_telegram("✅ تم تشغيل نظام الرصد بنجاح - جاري فتح التابات...")

    log("SYSTEM", f"🚀 بيفتح {len(SEARCH_TERMS)} تاب...")

    # كل تاب بيبدأ بعد التاني بـ 5 ثواني
    tasks = []
    for i, term in enumerate(SEARCH_TERMS):
        task = asyncio.create_task(monitor_tab(context, term, start_delay=i * 5))
        tasks.append(task)
        state["monitoring_tasks"] = tasks

# ====================================================
#  تنظيف
# ====================================================
async def cleanup_browser():
    state["is_running"] = False
    state["login_ready"] = False

    for task in state.get("monitoring_tasks", []):
        task.cancel()
    state["monitoring_tasks"] = []

    await asyncio.sleep(2)

    if state.get("login_page"):
        try:
            if not state["login_page"].is_closed():
                await state["login_page"].close()
        except:
            pass
        state["login_page"] = None

    if state.get("context"):
        try:
            await state["context"].close()
        except:
            pass
        state["context"] = None

    if state.get("browser"):
        try:
            await state["browser"].close()
        except:
            pass
        state["browser"] = None

    if state.get("playwright"):
        try:
            await state["playwright"].stop()
        except:
            pass
        state["playwright"] = None

    state["active_tabs"] = 0
    state["is_logged_in"] = False
    state["status"] = "stopped"

# ====================================================
#  FastAPI
# ====================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    send_telegram("✅ تم تشغيل السيرفر — في انتظار تسجيل الدخول")
    yield
    await cleanup_browser()

app = FastAPI(title="Beheira Security Monitor", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ---- Auth ----
@app.post("/login")
async def login(username: str = None, password: str = None):
    import json
    body = {}
    try:
        from starlette.requests import Request
    except:
        pass
    if username == ADMIN_USER and password == ADMIN_PASS:
        token = f"token-{time.time()}"
        tokens[token] = True
        return {"token": token, "message": "تم تسجيل الدخول بنجاح"}
    raise HTTPException(401, "بيانات غير صحيحة")

@app.post("/login")
async def login_json(request: dict = None):
    pass

from starlette.requests import Request as StarletteRequest

@app.post("/login", include_in_schema=False)
async def login_endpoint(request: StarletteRequest):
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")
    if username == ADMIN_USER and password == ADMIN_PASS:
        token = f"token-{time.time()}"
        tokens[token] = True
        return {"token": token, "message": "تم تسجيل الدخول بنجاح"}
    raise HTTPException(401, "بيانات غير صحيحة")

# ---- Facebook Login Step 1: فتح المتصفح وإدخال البيانات ----
@app.post("/account/login")
async def fb_login(email: str = Query(...), password: str = Query(...)):
    state["fb_password"] = password
    asyncio.create_task(open_browser_and_login(email, password))
    return {"success": True, "message": "جاري فتح المتصفح وتسجيل الدخول... انتظر ثم اضغط تم"}

# ---- Facebook Login Step 2: تأكيد وبدء المراقبة ----
@app.post("/account/confirm-login")
async def confirm_fb_login():
    if not state.get("context"):
        raise HTTPException(400, "المتصفح مش مفتوح - سجل دخول الأول")
    
    asyncio.create_task(start_monitoring())
    return {"success": True, "message": "تم! جاري فتح تابات المراقبة..."}

# ---- Alerts ----
@app.get("/alerts")
async def get_alerts():
    return alerts_store

@app.get("/alerts/latest")
async def get_latest():
    return alerts_store[0] if alerts_store else None

# ---- Status ----
@app.get("/status")
async def get_status():
    return {
        "status": state["status"],
        "last_scrape": state["last_scrape"],
        "active_tabs": state["active_tabs"],
        "total_alerts": state["total_alerts"],
        "fb_email": state["fb_email"],
    }

@app.get("/account/status")
async def account_status():
    return {
        "status": "active" if state["is_logged_in"] else ("waiting" if state["login_ready"] else "inactive"),
        "is_logged_in": state["is_logged_in"],
        "is_running": state["is_running"],
    }

# ---- Stop ----
@app.post("/account/stop")
async def stop_monitoring():
    await cleanup_browser()
    return {"success": True, "message": "تم إيقاف النظام"}

# ---- Open Browser (legacy) ----
@app.get("/open-browser")
async def open_browser():
    if not state["is_logged_in"]:
        return {"success": False, "message": "سجل دخول Facebook أولاً لبدء المتصفح"}
    return {"success": True, "message": "المتصفح يعمل بالفعل"}

# ---- Placeholder endpoints ----
@app.get("/google-alerts")
async def google_alerts():
    return []

@app.get("/news")
async def news():
    return []
