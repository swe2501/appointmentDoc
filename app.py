#!/usr/bin/env python3
import os
import uuid
import time
import threading
import requests
from flask import Flask, render_template, request, jsonify

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

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

FOLLOWER_THRESHOLD = 50_000
TAIWAN_KEYWORDS = [
    "台灣", "台北", "台中", "台南", "高雄", "新北", "桃園", "新竹", "基隆",
    "Taiwan", "Taipei", "Taichung", "Tainan", "Kaohsiung", "Hsinchu",
    "#台灣", "#taiwan", "#taipei", "🇹🇼",
]
CN_KEYWORDS = ["北京", "上海", "广州", "深圳", "成都", "武汉", "杭州", "中国", "大陆", "重庆"]

# Instagram mobile app headers — less restricted than web GraphQL
MOBILE_HEADERS = {
    "User-Agent": (
        "Instagram 275.0.0.27.98 Android "
        "(33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458229258)"
    ),
    "X-IG-App-ID": "936619743392459",
    "X-IG-Capabilities": "3brTvw==",
    "X-IG-Connection-Type": "WIFI",
    "Accept-Language": "en-US",
    "Accept": "*/*",
}


def is_taiwanese(bio: str, full_name: str) -> bool:
    bio = bio.lower()
    full_name = full_name.lower()
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


def build_mobile_session(session_id: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update(MOBILE_HEADERS)
    sess.cookies.set("sessionid", session_id, domain=".instagram.com", path="/")
    return sess


def get_user_id(sess: requests.Session, username: str) -> str:
    r = sess.get(
        "https://i.instagram.com/api/v1/users/web_profile_info/",
        params={"username": username},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data["data"]["user"]["id"]


def fetch_all_following(sess: requests.Session, user_id: str, update_fn) -> list[dict]:
    """Paginate through /friendships/{user_id}/following/ (200 per page)."""
    all_users = []
    max_id = None
    page = 0

    while True:
        page += 1
        params = {"count": 200}
        if max_id:
            params["max_id"] = max_id

        r = sess.get(
            f"https://i.instagram.com/api/v1/friendships/{user_id}/following/",
            params=params,
            timeout=20,
        )
        if r.status_code == 401:
            raise PermissionError("登入失效，請重新登入。")
        if r.status_code != 200:
            raise RuntimeError(f"取得追蹤清單失敗（{r.status_code}）：{r.text[:200]}")

        data = r.json()
        batch = data.get("users", [])
        all_users.extend(batch)
        update_fn(f"取得追蹤清單... 第 {page} 頁，已取得 {len(all_users)} 人")

        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(1)

    return all_users


def fetch_user_detail(sess: requests.Session, user_id: str) -> dict | None:
    """Get full profile info (includes follower_count) for a single user."""
    try:
        r = sess.get(
            f"https://i.instagram.com/api/v1/users/{user_id}/info/",
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("user", {})
    except Exception:
        pass
    return None


def run_filter_job(job_id: str, username: str, password: str, two_fa_code: str | None,
                   min_followers: int, taiwan_only: bool, session_cookie: str | None = None):

    def update(status=None, progress=None, error=None, needs_2fa=False):
        with jobs_lock:
            if status:
                jobs[job_id]["status"] = status
            if progress is not None:
                jobs[job_id]["progress"] = progress
            if error:
                jobs[job_id]["error"] = error
            if needs_2fa:
                jobs[job_id]["needs_2fa"] = True

    # ── Step 1: get sessionid ────────────────────────────────────────────────
    update(progress="正在登入 Instagram...")

    if session_cookie:
        sessionid = session_cookie
    else:
        # Use instaloader only for the login flow
        loader = instaloader.Instaloader(quiet=True, sleep=True, max_connection_attempts=3,
                                         download_pictures=False, download_videos=False,
                                         download_video_thumbnails=False, download_geotags=False,
                                         download_comments=False, save_metadata=False)
        try:
            loader.login(username, password)
        except instaloader.exceptions.BadCredentialsException:
            update(status="error", error="帳號或密碼錯誤，請確認後重試。")
            return
        except instaloader.exceptions.TwoFactorAuthRequiredException:
            if not two_fa_code:
                update(status="needs_2fa", needs_2fa=True, progress="需要雙重驗證碼")
                return
            try:
                loader.two_factor_login(two_fa_code)
            except Exception as e:
                update(status="error", error=f"雙重驗證失敗：{e}")
                return
        except Exception as e:
            update(status="error", error=f"登入失敗：{str(e)}")
            return

        # Extract sessionid from instaloader's requests session
        sessionid = loader.context._session.cookies.get("sessionid", domain=".instagram.com")
        if not sessionid:
            update(status="error", error="無法取得登入 session，請重試。")
            return

    # ── Step 2: use mobile API ───────────────────────────────────────────────
    sess = build_mobile_session(sessionid)

    try:
        update(progress="取得用戶資訊...")
        user_id = get_user_id(sess, username)
    except Exception as e:
        update(status="error", error=f"無法取得用戶資訊：{str(e)}")
        return

    try:
        following = fetch_all_following(sess, user_id, lambda p: update(progress=p))
    except PermissionError as e:
        update(status="error", error=str(e))
        return
    except Exception as e:
        update(status="error", error=str(e))
        return

    total = len(following)
    update(progress=f"共追蹤 {total} 人，開始掃描粉絲數...")

    for i, user in enumerate(following):
        with jobs_lock:
            if jobs[job_id].get("cancelled"):
                break

        if i > 0 and i % 20 == 0:
            time.sleep(1)

        uid = user.get("pk") or user.get("id")
        uname = user.get("username", "")
        pct = int((i + 1) / total * 100)
        update(progress=f"掃描中 {i+1}/{total}（{pct}%）— @{uname}")

        # follower_count is often included in the following list response
        follower_count = user.get("follower_count")
        bio = user.get("biography") or ""
        full_name = user.get("full_name") or ""

        # If follower_count missing, fetch full profile
        if follower_count is None:
            detail = fetch_user_detail(sess, uid)
            if detail:
                follower_count = detail.get("follower_count", 0)
                bio = detail.get("biography") or bio
                full_name = detail.get("full_name") or full_name
            time.sleep(0.5)

        if not follower_count or follower_count < min_followers:
            continue

        if taiwan_only and not is_taiwanese(bio, full_name):
            continue

        entry = {
            "username": uname,
            "full_name": full_name,
            "followers": follower_count,
            "biography": bio.replace("\n", " "),
            "url": f"https://www.instagram.com/{uname}/",
            "is_verified": user.get("is_verified", False),
            "profile_pic": user.get("profile_pic_url", ""),
        }
        with jobs_lock:
            jobs[job_id]["results"].append(entry)

    with jobs_lock:
        jobs[job_id]["results"].sort(key=lambda x: x["followers"], reverse=True)

    update(status="done", progress=f"完成！找到 {len(jobs[job_id]['results'])} 個符合條件的帳號")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    """Accept raw following list from browser console script and filter it."""
    data = request.json or {}
    users = data.get("users") or []
    min_followers = int(data.get("min_followers") or FOLLOWER_THRESHOLD)
    taiwan_only = bool(data.get("taiwan_only", True))

    if not users:
        return jsonify({"error": "沒有收到資料"}), 400

    results = []
    for u in users:
        followers = u.get("followers") or u.get("follower_count") or 0
        if followers < min_followers:
            continue
        bio = u.get("biography") or ""
        full_name = u.get("full_name") or ""
        if taiwan_only and not is_taiwanese(bio, full_name):
            continue
        results.append({
            "username": u.get("username", ""),
            "full_name": full_name,
            "followers": followers,
            "biography": bio.replace("\n", " "),
            "url": u.get("url") or f"https://www.instagram.com/{u.get('username', '')}/",
            "is_verified": bool(u.get("is_verified")),
            "profile_pic": u.get("profile_pic") or u.get("profile_pic_url") or "",
        })

    results.sort(key=lambda x: x["followers"], reverse=True)
    return jsonify({"results": results, "count": len(results)})


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

    threading.Thread(
        target=run_filter_job,
        args=(job_id, username, password, two_fa_code, min_followers, taiwan_only, session_cookie),
        daemon=True,
    ).start()

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
