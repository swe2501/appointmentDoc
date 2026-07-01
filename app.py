#!/usr/bin/env python3
import os
import uuid
import time
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, session

try:
    import instaloader
except ImportError:
    instaloader = None

try:
    from langdetect import detect, DetectorFactory
    from langdetect.lang_detect_exception import LangDetectException
    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# In-memory job store
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

FOLLOWER_THRESHOLD = 50_000
TAIWAN_KEYWORDS = [
    "台灣", "台北", "台中", "台南", "高雄", "新北", "桃園", "新竹", "基隆",
    "Taiwan", "Taipei", "Taichung", "Tainan", "Kaohsiung", "Hsinchu",
    "#台灣", "#taiwan", "#taipei", "🇹🇼",
]
CN_KEYWORDS = ["北京", "上海", "广州", "深圳", "成都", "武汉", "杭州", "中国", "大陆", "重庆"]


def is_taiwanese(profile) -> bool:
    bio = (profile.biography or "").lower()
    full_name = (profile.full_name or "").lower()
    for kw in TAIWAN_KEYWORDS:
        if kw.lower() in bio or kw.lower() in full_name:
            return True
    if LANGDETECT_AVAILABLE and bio.strip():
        try:
            lang = detect(bio)
            if lang in ("zh-tw", "zh"):
                if not any(kw in bio for kw in CN_KEYWORDS):
                    return True
        except LangDetectException:
            pass
    return False


def login_with_cookie(loader, username: str, session_cookie: str) -> str | None:
    """用 sessionid cookie 登入，回傳錯誤訊息或 None（成功）"""
    loader.context._session.cookies.set(
        "sessionid", session_cookie, domain=".instagram.com", path="/"
    )
    loader.context._session.cookies.set(
        "ig_did", "0", domain=".instagram.com", path="/"
    )
    try:
        # 驗證 cookie 是否有效
        profile = instaloader.Profile.from_username(loader.context, username)
        loader.context.username = profile.username
        return None
    except instaloader.exceptions.LoginRequiredException:
        return "Cookie 無效或已過期，請重新取得 sessionid。"
    except Exception as e:
        return f"Cookie 登入失敗：{str(e)}"


def run_filter_job(job_id: str, username: str, password: str, two_fa_code: str | None,
                   min_followers: int, taiwan_only: bool, session_cookie: str | None = None):
    def update(status=None, progress=None, error=None, results=None, needs_2fa=False, loader_ref=None):
        with jobs_lock:
            if status:
                jobs[job_id]["status"] = status
            if progress is not None:
                jobs[job_id]["progress"] = progress
            if error:
                jobs[job_id]["error"] = error
            if results is not None:
                jobs[job_id]["results"] = results
            if needs_2fa:
                jobs[job_id]["needs_2fa"] = True
            if loader_ref:
                jobs[job_id]["_loader"] = loader_ref

    loader = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        sleep=True,
        max_connection_attempts=3,
    )

    update(progress="正在登入 Instagram...")

    if session_cookie:
        # Cookie 登入（適合 Facebook 連動帳號）
        err = login_with_cookie(loader, username, session_cookie)
        if err:
            update(status="error", error=err)
            return
    else:
        # 帳密登入
        try:
            loader.login(username, password)
        except instaloader.exceptions.BadCredentialsException:
            update(status="error", error="帳號或密碼錯誤，請確認後重試。")
            return
        except instaloader.exceptions.TwoFactorAuthRequiredException:
            if not two_fa_code:
                update(status="needs_2fa", needs_2fa=True, progress="需要雙重驗證碼")
                update(loader_ref=loader)
                return
            try:
                loader.two_factor_login(two_fa_code)
            except Exception as e:
                update(status="error", error=f"雙重驗證失敗：{e}")
                return
        except Exception as e:
            update(status="error", error=f"登入失敗：{str(e)}")
            return

    try:
        update(progress="正在取得追蹤清單...")
        profile = instaloader.Profile.from_username(loader.context, username)
        followees = list(profile.get_followees())
    except Exception as e:
        update(status="error", error=f"無法取得追蹤清單：{str(e)}")
        return

    total = len(followees)
    update(progress=f"共追蹤 {total} 個帳號，開始掃描...")

    results = []
    for i, followee in enumerate(followees):
        with jobs_lock:
            if jobs[job_id].get("cancelled"):
                break

        if i > 0 and i % 10 == 0:
            time.sleep(1.5)

        pct = int((i + 1) / total * 100)
        update(progress=f"掃描中 {i+1}/{total}（{pct}%）— @{followee.username}")

        try:
            followers = followee.followers
        except Exception:
            continue

        if followers < min_followers:
            continue

        if taiwan_only and not is_taiwanese(followee):
            continue

        entry = {
            "username": followee.username,
            "full_name": followee.full_name or "",
            "followers": followers,
            "biography": (followee.biography or "").replace("\n", " "),
            "url": f"https://www.instagram.com/{followee.username}/",
            "is_verified": followee.is_verified,
            "profile_pic": followee.profile_pic_url,
        }
        with jobs_lock:
            jobs[job_id]["results"].append(entry)

    # Final sort
    with jobs_lock:
        jobs[job_id]["results"].sort(key=lambda x: x["followers"], reverse=True)

    update(status="done", progress=f"完成！找到 {len(jobs[job_id]['results'])} 個符合條件的帳號")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    two_fa_code = (data.get("two_fa_code") or "").strip() or None
    session_cookie = (data.get("session_cookie") or "").strip() or None
    min_followers = int(data.get("min_followers") or FOLLOWER_THRESHOLD)
    taiwan_only = bool(data.get("taiwan_only", True))

    if not username:
        return jsonify({"error": "請輸入 IG 帳號"}), 400
    if not session_cookie and not password:
        return jsonify({"error": "請輸入密碼或 sessionid"}), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "running",
            "progress": "準備中...",
            "results": [],
            "error": None,
            "needs_2fa": False,
        }

    thread = threading.Thread(
        target=run_filter_job,
        args=(job_id, username, password, two_fa_code, min_followers, taiwan_only, session_cookie),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "找不到此任務"}), 404

    return jsonify({
        "status": job["status"],
        "progress": job["progress"],
        "results": job["results"],
        "error": job.get("error"),
        "needs_2fa": job.get("needs_2fa", False),
        "count": len(job["results"]),
    })


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id: str):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["cancelled"] = True
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
