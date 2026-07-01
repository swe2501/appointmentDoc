#!/usr/bin/env python3
"""
Instagram Following Filter
過濾你追蹤的 IG 帳號：粉絲數 > 50,000 且為台灣人
"""

import sys
import time
import json
import argparse
from pathlib import Path
from getpass import getpass

try:
    import instaloader
except ImportError:
    print("請先安裝依賴：pip install -r requirements.txt")
    sys.exit(1)

try:
    from langdetect import detect
    from langdetect.lang_detect_exception import LangDetectException
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


TAIWAN_KEYWORDS = [
    "台灣", "台北", "台中", "台南", "高雄", "新北", "桃園",
    "Taiwan", "Taipei", "Taichung", "Tainan", "Kaohsiung",
    "#台灣", "#台北", "#taiwan", "#taipei",
    "🇹🇼",
]

FOLLOWER_THRESHOLD = 50_000


def is_taiwanese(profile: instaloader.Profile) -> bool:
    """判斷帳號是否為台灣人（依據個人簡介與位置）"""
    bio = (profile.biography or "").lower()
    full_name = (profile.full_name or "").lower()

    # 檢查台灣關鍵字
    for kw in TAIWAN_KEYWORDS:
        if kw.lower() in bio or kw.lower() in full_name:
            return True

    # 用語言偵測判斷繁體中文
    if LANGDETECT_AVAILABLE and bio.strip():
        try:
            lang = detect(bio)
            # zh-tw / zh-cn 都算中文，再配合關鍵字排除簡中
            if lang in ("zh-tw", "zh"):
                # 排除明顯的中國大陸城市
                cn_keywords = ["北京", "上海", "广州", "深圳", "成都", "武汉", "杭州", "中国", "大陆"]
                if not any(kw in bio for kw in cn_keywords):
                    return True
        except LangDetectException:
            pass

    return False


def load_session(loader: instaloader.Instaloader, username: str, session_file: str | None) -> bool:
    """嘗試載入既有 session，失敗則要求登入"""
    if session_file and Path(session_file).exists():
        try:
            loader.load_session_from_file(username, session_file)
            print(f"✓ 已從 {session_file} 載入 session")
            return True
        except Exception as e:
            print(f"載入 session 失敗：{e}")

    # 互動式登入
    password = getpass(f"請輸入 {username} 的 IG 密碼：")
    try:
        loader.login(username, password)
        if session_file:
            loader.save_session_to_file(session_file)
            print(f"✓ Session 已儲存至 {session_file}")
        return True
    except instaloader.exceptions.BadCredentialsException:
        print("帳號或密碼錯誤，請重試。")
        return False
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        code = input("請輸入雙重驗證碼：")
        try:
            loader.two_factor_login(code)
            if session_file:
                loader.save_session_to_file(session_file)
            return True
        except Exception as e:
            print(f"雙重驗證失敗：{e}")
            return False
    except Exception as e:
        print(f"登入失敗：{e}")
        return False


def filter_following(
    username: str,
    session_file: str | None = None,
    output_file: str | None = None,
    taiwan_only: bool = True,
    min_followers: int = FOLLOWER_THRESHOLD,
) -> list[dict]:
    """
    取得指定帳號的追蹤清單，並過濾出粉絲數超過門檻且（選擇性地）為台灣人的帳號。
    """
    loader = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        sleep=True,           # 自動 rate-limit 保護
        max_connection_attempts=3,
    )

    if not load_session(loader, username, session_file):
        sys.exit(1)

    print(f"\n正在取得 @{username} 的追蹤清單...")
    try:
        profile = instaloader.Profile.from_username(loader.context, username)
        followees = list(profile.get_followees())
    except instaloader.exceptions.LoginRequiredException:
        print("需要登入才能取得追蹤清單。")
        sys.exit(1)

    total = len(followees)
    print(f"共追蹤 {total} 個帳號，開始篩選（粉絲 > {min_followers:,}{'，限台灣帳號' if taiwan_only else ''}）...\n")

    results = []
    iterator = tqdm(followees, desc="掃描中", unit="帳號") if TQDM_AVAILABLE else followees

    for i, followee in enumerate(iterator):
        # Rate-limit 保護：每 10 個請求暫停 2 秒
        if i > 0 and i % 10 == 0 and not TQDM_AVAILABLE:
            print(f"  進度：{i}/{total}")
            time.sleep(2)

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
            "full_name": followee.full_name,
            "followers": followers,
            "biography": followee.biography,
            "url": f"https://www.instagram.com/{followee.username}/",
            "is_verified": followee.is_verified,
        }
        results.append(entry)

    # 依粉絲數降序排列
    results.sort(key=lambda x: x["followers"], reverse=True)

    _print_results(results, taiwan_only)

    if output_file:
        Path(output_file).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✓ 結果已儲存至 {output_file}")

    return results


def _print_results(results: list[dict], taiwan_only: bool) -> None:
    label = "台灣帳號且粉絲 > 50,000" if taiwan_only else f"粉絲 > {FOLLOWER_THRESHOLD:,}"
    print(f"\n{'='*60}")
    print(f"符合條件（{label}）：共 {len(results)} 個帳號")
    print("="*60)
    for r in results:
        verified = " ✓" if r["is_verified"] else ""
        print(f"@{r['username']}{verified}  ({r['followers']:,} 粉絲)")
        if r["full_name"]:
            print(f"  名稱：{r['full_name']}")
        if r["biography"]:
            bio_preview = r["biography"].replace("\n", " ")[:80]
            print(f"  簡介：{bio_preview}")
        print(f"  連結：{r['url']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="過濾 IG 追蹤清單：粉絲數 > 50,000 且為台灣人"
    )
    parser.add_argument("username", help="你的 Instagram 帳號名稱（不含 @）")
    parser.add_argument(
        "--session-file", "-s",
        default=None,
        help="Session 檔案路徑（用於儲存/載入登入狀態，避免重複輸入密碼）",
    )
    parser.add_argument(
        "--output", "-o",
        default="results.json",
        help="輸出 JSON 檔案路徑（預設：results.json）",
    )
    parser.add_argument(
        "--min-followers", "-m",
        type=int,
        default=FOLLOWER_THRESHOLD,
        help=f"最低粉絲數門檻（預設：{FOLLOWER_THRESHOLD:,}）",
    )
    parser.add_argument(
        "--no-taiwan-filter",
        action="store_true",
        help="停用台灣帳號過濾（只依粉絲數篩選）",
    )

    args = parser.parse_args()

    filter_following(
        username=args.username,
        session_file=args.session_file,
        output_file=args.output,
        taiwan_only=not args.no_taiwan_filter,
        min_followers=args.min_followers,
    )


if __name__ == "__main__":
    main()
