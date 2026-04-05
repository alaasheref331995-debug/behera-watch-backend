import asyncio
import re
import os
import json
import requests
import urllib.parse
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright

# ====================================================
#  إعدادات
# ====================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8675425131:AAFis74NgxGC0KT96XB14qTBT_AL3JeH0to")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5134151930")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin")

# ====================================================
#  كلمات البحث
# ====================================================
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

HIGH_KEYWORDS = ["سلاح", "استغاثة", "استغاثه", "الحقوني", "الحقونا", "عاجل", "غرق", "غريق", "انهيار"]
MEDIUM_KEYWORDS = ["حادث", "حادثة", "تصادم", "سرقة", "مشاجرة", "بلطجة", "بلطجية", "تحرش"]

# ====================================================
#  حالة النظام
# ====================================================
system_state = {
    "browser": None,
    "context": None,
    "playwright": None,
    "is_running": False,
    "is_logged_in": False,
    "status": "inactive",
    "alerts": [],
    "monitor_tasks": [],
    "last_scrape": None,
    "fb_email": None,
}

MAX_ALERTS = 50

# ====================================================
#  دوال مساعدة
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

def classify_alert(found_keywords):
    for kw in found_keywords:
        cleaned = clean_arabic_text(kw)
        for high in HIGH_KEYWORDS:
            if clean_arabic_text(high) in cleaned:
                return "high", "استغاثة"
        for med in MEDIUM_KEYWORDS:
            if clean_arabic_text(med) in cleaned:
                return "medium", "حادث"
    return "low", "خبر"

def detect_location(text):
    for term in SEARCH_TERMS:
        if clean_arabic_text(term) in clean_arabic_text(text):
            return term
    return "البحيرة"

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

async def activate_most_recent(page, term):
    try:
        await close_popups(page)
        await asyncio.sleep(3)

        all_clicked = False
        for _ in range(3):
            try:
                for el in await page.get_by_text("All", exact=True).all():
                    if await el.is_visible():
                        await el.click()
                        log(term, "✅ تم الضغط على 'All'")
                        await asyncio.sleep(3)
                        all_clicked = True
                        break
            except:
                pass
            if all_clicked: break
            await asyncio.sleep(2)

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
                        log(term, f"✅ تم تفعيل Recent Posts (محاولة {attempt})")
                        await asyncio.sleep(3)
                        recent_activated = True
                        break
                except:
                    pass
            if recent_activated: break

            try:
                toggles = await page.query_selector_all("input[type='checkbox'], div[role='switch']")
                for tog in toggles:
                    if await tog.is_visible():
                        await tog.click()
                        log(term, f"✅ toggle fallback (محاولة {attempt})")
                        await asyncio.sleep(3)
                        recent_activated = True
                        break
            except:
                pass
            if recent_activated: break

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
#  رصد تاب واحد
# ====================================================
async def monitor_tab(context, search_term, start_delay=0):
    if start_delay > 0:
        await asyncio.sleep(start_delay)

    encoded = urllib.parse.quote(search_term)
    search_url = f"https://www.facebook.com/search/posts/?q={encoded}"
    seen_posts = set()

    while system_state["is_running"]:
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

            while system_state["is_running"]:
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

                                priority, alert_type = classify_alert(found_alerts)
                                location = detect_location(full_text)
                                now = datetime.now()

                                alert_obj = {
                                    "id": f"alert-{now.timestamp()}-{post_id}",
                                    "location": location,
                                    "type": alert_type,
                                    "priority": priority,
                                    "status": "confirmed",
                                    "text": full_text[:200],
                                    "full_text": full_text,
                                    "post_link": post_url,
                                    "timestamp": now.isoformat(),
                                    "is_new": True,
                                    "keywords": found_alerts,
                                    "search_term": search_term,
                                }

                                system_state["alerts"].insert(0, alert_obj)
                                if len(system_state["alerts"]) > MAX_ALERTS:
                                    system_state["alerts"] = system_state["alerts"][:MAX_ALERTS]
                                system_state["last_scrape"] = now.isoformat()

                                msg = (
                                    f"🔔 <b>منشور مكتشف جديد</b>\n\n"
                                    f"🔎 <b>بحث:</b> {search_term}\n"
                                    f"📍 <b>المكان:</b> {location}\n"
                                    f"🎯 <b>النوع:</b> {alert_type} ({priority})\n"
                                    f"🎯 <b>الكلمات:</b> {', '.join(found_alerts)}\n"
                                    f"⏰ <b>الوقت:</b> {now.strftime('%I:%M %p')}\n"
                                    f"🔗 <b>الرابط:</b> {post_url}\n"
                                    f"---------------------------\n"
                                    f"📝 <b>النص:</b>\n{full_text[:350]}..."
                                )
                                send_telegram(msg)
                                log(search_term, f"✅ تم رصد: {found_alerts}")

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
            log(search_term, f"⚠️ خطأ — هيعيد فتح التاب بعد 15 ثانية: {e}")
            try:
                if page and not page.is_closed():
                    await page.close()
            except:
                pass
            await asyncio.sleep(15)

# ====================================================
#  تشغيل المراقبة
# ====================================================
async def start_monitoring(email: str, password: str):
    """يفتح متصفح، يسجل دخول فيسبوك، ويبدأ الرصد"""
    await stop_monitoring()

    try:
        pw = await async_playwright().start()
        system_state["playwright"] = pw

        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-infobars",
            ]
        )
        system_state["browser"] = browser

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ar-EG",
            timezone_id="Africa/Cairo",
        )
        system_state["context"] = context

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ar', 'en-US'] });
            window.chrome = { runtime: {} };
        """)

        # تسجيل الدخول في فيسبوك
        login_page = await context.new_page()
        await login_page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        await login_page.fill("input[name='email']", email)
        await asyncio.sleep(1)
        await login_page.fill("input[name='pass']", password)
        await asyncio.sleep(1)
        await login_page.click("button[name='login']")
        await asyncio.sleep(8)

        # التحقق من نجاح تسجيل الدخول
        current_url = login_page.url
        if "login" in current_url or "checkpoint" in current_url:
            await login_page.close()
            await stop_monitoring()
            raise Exception("فشل تسجيل الدخول - تحقق من البيانات أو قد يكون هناك checkpoint")

        system_state["is_logged_in"] = True
        system_state["fb_email"] = email
        system_state["status"] = "active"
        await login_page.close()

        send_telegram(f"✅ <b>تم تسجيل الدخول بنجاح في Facebook</b>\n📧 الحساب: {email}\n🚀 جاري بدء الرصد...")

        # بدء المراقبة
        system_state["is_running"] = True

        tasks = []
        for i, term in enumerate(SEARCH_TERMS):
            task = asyncio.create_task(monitor_tab(context, term, start_delay=i * 5))
            tasks.append(task)
        system_state["monitor_tasks"] = tasks

        log("SYSTEM", f"🚀 تم تشغيل {len(SEARCH_TERMS)} تاب رصد")
        send_telegram(f"🚀 <b>تم تشغيل نظام الرصد بنجاح</b>\n📊 عدد التابات: {len(SEARCH_TERMS)}\n⏰ الوقت: {datetime.now().strftime('%I:%M %p')}")

        return True

    except Exception as e:
        log("SYSTEM", f"❌ خطأ في التشغيل: {e}")
        send_telegram(f"❌ <b>خطأ في تشغيل النظام:</b> {str(e)}")
        raise

async def stop_monitoring():
    """إيقاف المراقبة وإغلاق المتصفح"""
    system_state["is_running"] = False

    for task in system_state.get("monitor_tasks", []):
        task.cancel()
    system_state["monitor_tasks"] = []

    if system_state.get("context"):
        try: await system_state["context"].close()
        except: pass
    if system_state.get("browser"):
        try: await system_state["browser"].close()
        except: pass
    if system_state.get("playwright"):
        try: await system_state["playwright"].stop()
        except: pass

    system_state["browser"] = None
    system_state["context"] = None
    system_state["playwright"] = None
    system_state["is_logged_in"] = False
    system_state["status"] = "inactive"

# ====================================================
#  FastAPI
# ====================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🟢 السيرفر شغال...")
    yield
    await stop_monitoring()

app = FastAPI(title="Beheira SOC Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Auth ----
@app.post("/login")
async def login(data: dict):
    username = data.get("username", "")
    password = data.get("password", "")
    if username == ADMIN_USER and password == ADMIN_PASS:
        return {"token": f"token-{datetime.now().timestamp()}", "message": "تم تسجيل الدخول بنجاح"}
    raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

# ---- Facebook Login + Start Monitoring ----
@app.post("/account/login")
async def fb_login(email: str = Query(...), password: str = Query(...)):
    try:
        asyncio.create_task(start_monitoring(email, password))
        return {"success": True, "message": "جاري تسجيل الدخول وبدء الرصد... انتظر دقيقة"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---- Alerts ----
@app.get("/alerts")
async def get_alerts():
    return system_state["alerts"]

@app.get("/alerts/latest")
async def get_latest_alert():
    if system_state["alerts"]:
        return system_state["alerts"][0]
    return None

# ---- Status ----
@app.get("/status")
async def get_status():
    return {
        "status": "running" if system_state["is_running"] else "stopped",
        "last_scrape": system_state["last_scrape"],
        "active_tabs": len([t for t in system_state.get("monitor_tasks", []) if not t.done()]),
        "total_alerts": len(system_state["alerts"]),
        "fb_email": system_state.get("fb_email"),
    }

@app.get("/account/status")
async def get_account_status():
    return {
        "status": system_state["status"],
        "is_logged_in": system_state["is_logged_in"],
        "is_running": system_state["is_running"],
    }

# ---- Browser Control ----
@app.get("/open-browser")
async def open_browser():
    if system_state["is_running"]:
        return {"success": True, "message": "المتصفح يعمل بالفعل"}
    return {"success": False, "message": "سجل دخول Facebook أولاً لبدء المتصفح"}

@app.post("/stop")
async def stop():
    await stop_monitoring()
    send_telegram("⛔ <b>تم إيقاف نظام الرصد</b>")
    return {"success": True, "message": "تم إيقاف النظام"}

# ---- Google Alerts (placeholder) ----
@app.get("/google-alerts")
async def get_google_alerts():
    return []

@app.get("/news")
async def get_news():
    return []

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
