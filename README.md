# gemini_save

將 Google Gemini 分享對話儲存為本機檔案的命令列工具。

支援 Markdown、HTML、純文字三種輸出格式，可批次處理多個網址，並能正確保留數學公式、程式碼、表格、圖片等內容。

---

## 安裝

```bash
pip install requests beautifulsoup4 markdownify markdown
```

需要處理 JavaScript 動態載入的頁面時（建議安裝）：

```bash
pip install playwright && playwright install chromium
```

---

## 使用說明

### 基本語法

```
python gemini_save.py <url> [選項]
```

若省略 `url`，程式會以互動方式提示輸入。

### 選項說明

| 選項 | 說明 |
|------|------|
| `-o FILE` / `--output FILE` | 指定輸出檔案路徑（省略則自動命名） |
| `--format {both,html,markdown,text}` | 輸出格式；預設 `both`（同時產生 `.html` 和 `.md`） |
| `--playwright` | 強制使用 Playwright 無頭瀏覽器擷取（更可靠） |
| `--download-only` | 僅執行第一階段：下載並儲存原始 HTML，不進行轉換 |
| `--convert FILE` | 僅執行第二階段：將已存在的 `_raw.html` 轉換為輸出格式 |
| `--input-csv FILE` | 批次模式：從 CSV 讀取多組 `url[,title]` |
| `--debug-html PATH` | 將下載的原始 HTML 另存至指定路徑，方便偵錯 |

---

## 範例

### 基本下載（同時產生 HTML 和 Markdown）

```bash
python gemini_save.py https://gemini.google.com/share/061733af550c
# 輸出：gemini_061733af550c.html、gemini_061733af550c.md
```

### 指定檔名

```bash
python gemini_save.py https://gemini.google.com/share/061733af550c -o my_chat.md
```

### 僅輸出純文字

```bash
python gemini_save.py https://gemini.google.com/share/061733af550c --format text
```

### 使用 Playwright（JS 渲染，更穩定）

```bash
python gemini_save.py https://gemini.google.com/share/061733af550c --playwright
```

### 自動轉換「繼續對話」連結

貼上 `/share/continue/` 格式的分享連結時，工具會自動轉換為公開可讀的 `/share/<id>` 網址並繼續下載，無需手動修改：

```
Note: 'continue' link → trying public URL: https://gemini.google.com/share/061733af550c
```

### 分兩階段執行（先下載，後轉換）

```bash
# 第一階段：只下載原始 HTML
python gemini_save.py https://gemini.google.com/share/061733af550c --download-only

# 第二階段：將已下載的 HTML 轉換為輸出格式
python gemini_save.py --convert gemini_061733af550c_raw.html
```

### 批次處理（CSV 輸入）

準備 CSV 檔案，格式為 `url,title`（標題欄可選）：

```csv
url,title
https://gemini.google.com/share/abc123,量子力學筆記
https://gemini.google.com/share/def456,Python 最佳化技巧
```

```bash
python gemini_save.py --input-csv conversations.csv
```

CSV 也可以省略標題列，直接以 `url,title` 兩欄排列，工具會自動偵測。

### 儲存原始 HTML 供偵錯

```bash
python gemini_save.py https://gemini.google.com/share/061733af550c --debug-html raw.html
```

---

## HTML 輸出版面

HTML 輸出採仿聊天氣泡介面，版面設計注重可讀性：

### 寬版佈局

對話欄寬度固定為 **1200 px**，在寬螢幕上充分利用空間；氣泡最大佔 85% 欄寬，長段落不會擠成一行。

```
.chat { max-width: 1200px; margin: 0 auto; }
.bubble { max-width: 85%; }
```

使用者訊息靠右（藍底白字），模型回應靠左（白底），視覺上一目了然。

### 鍵盤導航

開啟 `.html` 檔後可用鍵盤快速捲動長對話：

| 按鍵 | 動作 |
|------|------|
| `Home` | 回到頁面頂端 |
| `End` | 跳至頁面底部 |
| `PageUp` | 向上捲動 90% 視窗高度 |
| `PageDown` | 向下捲動 90% 視窗高度 |

### 其他版面細節

- 程式碼區塊有橫向捲軸（`overflow-x: auto`），不會撐破版面
- 圖片自動限縮（`max-width: 100%`），不溢出氣泡
- 數學公式（KaTeX）區塊有橫向捲軸，避免長公式斷行
- 表格欄位有邊框和淡色標頭底色，易於閱讀

---

## 強處

### 多重擷取策略（自動降級）

工具依序嘗試四種解析方式，遇到失敗時自動切換：

1. **`data-turn-role` 屬性**（最可靠，對應 Gemini 標準 DOM 結構）
2. **CSS 類別選擇器**（針對已知的 Gemini/Bard HTML 類別）
3. **`AF_initDataCallback` JSON blob**（從頁面嵌入的 JavaScript 資料解析）
4. **純文字回退**（最後手段，仍可擷取基本內容）

### 自動重試機制

預設以 `requests` 輕量下載，若偵測到擷取內容過少，會自動改用 Playwright 無頭瀏覽器重試，無需手動介入。

### 豐富的內容保留

- **數學公式**：保留 KaTeX span、`<sub>`、`<sup>` 標籤，在 HTML 輸出中內嵌 KaTeX CSS 直接渲染
- **程式碼**：保留圍欄式程式碼區塊及語言提示（如 ` ```python `）
- **表格**：HTML 表格轉換為 Markdown 表格或 HTML `<table>`
- **圖片**：擷取圖片 URL 與 alt 文字，HTML 輸出自動限制最大寬度
- **格式化**：正確轉換粗體、斜體、標題、有序/無序列表

### 防呆設計

- `/share/continue/<id>` 連結自動轉換為公開 `/share/<id>` 網址
- 自動偵測登入頁面，中止並提示使用者
- 支援 Windows 控制台 UTF-8 輸出，正確顯示中文及特殊字元
- 輸出檔名自動過濾非法字元

---

## 測試

使用 `pytest` 執行完整測試套件（51 個測試）：

```bash
pytest test_gemini_save.py -v
```

測試涵蓋以下功能：

| 測試類別 | 說明 |
|----------|------|
| `_normalize_share_url()` | continue 連結轉換、公開連結直通 |
| `_clean()` | 去除 UI 前置詞（"You said"、"Gemini said"）、摺疊多餘空行 |
| `_has_content()` | 偵測元素是否有實際內容（含圖片、空白節點） |
| `_GeminiConverter` | HTML → Markdown 轉換：粗體、斜體、標題、列表、表格、程式碼、圖片、數學公式 |
| `format_markdown()` | Markdown 輸出：標題、來源 URL、跳過空訊息、保留表格/公式/程式碼 |
| `format_html()` | HTML 輸出：氣泡 CSS 類別、KaTeX 載入、圖片寬度、1200px 寬版、`<sub>`/`<sup>` 直通 |
| `format_text()` | 純文字輸出：`[You]`/`[Gemini]` 標籤、內容完整性 |

---

## 除錯

### 問題：擷取到的內容太少或空白

```bash
# 1. 加上 --debug-html 儲存原始 HTML 供檢查
python gemini_save.py <url> --debug-html raw.html

# 2. 改用 Playwright 重試（處理 JS 動態內容）
python gemini_save.py <url> --playwright
```

### 問題：頁面被偵測為登入頁面

確認使用的是公開分享網址（格式：`https://gemini.google.com/share/<id>`）。  
若手邊只有 `/share/continue/` 連結，直接貼上即可，工具會自動嘗試轉換。

### 問題：已有 `_raw.html` 但輸出格式不對

不必重新下載，直接對已儲存的 HTML 重新轉換：

```bash
python gemini_save.py --convert <file>_raw.html --format markdown
```

### 問題：Windows 控制台顯示亂碼

工具已自動將 stdout 設為 UTF-8。若仍有問題，可在執行前設定環境變數：

```bash
set PYTHONIOENCODING=utf-8
python gemini_save.py <url>
```

---

## 依賴套件

| 套件 | 版本 | 用途 |
|------|------|------|
| `requests` | ≥2.31 | HTTP 下載 |
| `beautifulsoup4` | ≥4.12 | HTML 解析 |
| `markdownify` | ≥0.11 | HTML → Markdown 轉換 |
| `markdown` | ≥3.5 | Markdown → HTML 渲染（HTML 輸出用） |
| `playwright` | ≥1.40 | 無頭瀏覽器（選用，JS 渲染用） |

---

## 注意事項

- 僅支援**公開分享連結**（`/share/<id>`）；`/share/continue/` 連結會自動轉換，但若頁面實際上需要登入則仍無法取得內容。
- Google 若更改 Gemini 的 HTML 結構，CSS 選擇器可能需要跟著更新。
- Playwright 為選用依賴，但遇到動態載入頁面時強烈建議安裝。
