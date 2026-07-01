# Instagram Following Filter｜IG 追蹤過濾器

過濾你追蹤的 IG 帳號，找出**粉絲數超過 5 萬**且**為台灣人**的帳號。

## 安裝

```bash
pip install -r requirements.txt
```

## 使用方式

### 基本用法（過濾台灣帳號 + 粉絲 > 50,000）

```bash
python ig_filter.py 你的IG帳號
```

執行後會提示輸入密碼（密碼不會顯示在畫面上）。

### 儲存 Session（避免每次都要輸入密碼）

```bash
python ig_filter.py 你的IG帳號 --session-file my_session
```

第一次登入後 session 會儲存到 `my_session` 檔案，之後直接讀取不需再輸入密碼。

### 指定輸出檔案

```bash
python ig_filter.py 你的IG帳號 --output taiwan_kol.json
```

### 只依粉絲數篩選（不限台灣）

```bash
python ig_filter.py 你的IG帳號 --no-taiwan-filter
```

### 自訂粉絲數門檻

```bash
python ig_filter.py 你的IG帳號 --min-followers 100000
```

### 完整參數

```
usage: ig_filter.py [-h] [--session-file FILE] [--output FILE]
                    [--min-followers N] [--no-taiwan-filter]
                    username

positional arguments:
  username              你的 Instagram 帳號名稱（不含 @）

options:
  -h, --help            顯示說明
  --session-file, -s    Session 檔案路徑
  --output, -o          輸出 JSON 路徑（預設：results.json）
  --min-followers, -m   最低粉絲數門檻（預設：50000）
  --no-taiwan-filter    停用台灣帳號過濾
```

## 台灣帳號判斷邏輯

1. **關鍵字比對**：個人簡介或名稱包含「台灣」「台北」「🇹🇼」「Taiwan」等
2. **語言偵測**：簡介為繁體中文（排除明顯的中國大陸城市關鍵字）

## 輸出格式

```json
[
  {
    "username": "example_user",
    "full_name": "範例帳號",
    "followers": 123456,
    "biography": "個人簡介...",
    "url": "https://www.instagram.com/example_user/",
    "is_verified": false
  }
]
```

## 注意事項

- 本工具使用 [instaloader](https://instaloader.github.io/) 存取 Instagram
- 追蹤清單很大時掃描需要一段時間（有內建 rate-limit 保護避免被封鎖）
- 請勿短時間內大量執行，以免帳號被暫時限制
