# Q-Tracker for Windows

แอป Windows 11 สำหรับดู quota ของ **Codex**, **Antigravity (AGY)** และ **Gemini CLI** บน taskbar พร้อม system tray, dashboard, การรวม token usage ของ Codex อัตโนมัติ และการแจ้งเตือนเมื่อ quota ต่ำ

ข้อมูลอยู่ในเครื่องทั้งหมด ไม่มี telemetry และไม่เก็บ access token ลง state หรือ log

## ติดตั้งและเปิดใช้

เปิด PowerShell ในโฟลเดอร์โปรเจกต์ แล้วรันครั้งเดียว:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

สคริปต์จะหา/ติดตั้ง Python 3.13 แบบ current-user, สร้าง `.venv`, ติดตั้ง dependency และรัน tests จากนั้นเปิดโปรแกรมด้วย:

```powershell
.\run.bat
```

`run.bat` จะเลือก executable ที่ build แล้วก่อน หากยังไม่มีจะใช้ `.venv\Scripts\pythonw.exe` จึงไม่เปิดหน้าต่าง console ค้างไว้

## การใช้งาน

- ดับเบิลคลิกข้อความบน taskbar หรือ tray icon เพื่อเปิด dashboard
- คลิกขวาที่ taskbar/tray เพื่อ Refresh, ดู token summary หรือ Exit
- Dashboard แสดง quota จริง, เวลาจน reset, source, freshness และยอด token วันนี้/เดือนนี้/ทั้งหมด
- เปิด `Start with Windows` ใน dashboard เพื่อเพิ่มเฉพาะค่า `Q-Tracker` ใต้ HKCU; ไม่ต้องใช้สิทธิ์ admin
- การแจ้งเตือนเริ่มที่ 20%, 10% และ 5% และแจ้งเพียงครั้งเดียวต่อ threshold/reset window

### สัญลักษณ์สถานะ

| สถานะ | เครื่องหมาย | ความหมาย |
|---|---:|---|
| `ok` | ไม่มี | ยืนยันข้อมูลจาก source สำเร็จ |
| `stale` | `~` | แสดงค่าล่าสุดที่ยังมีประโยชน์ แต่ source ยังไม่ยืนยันข้อมูลใหม่ |
| `error`, `unavailable`, `rate_limited` | `!` | refresh มีปัญหา; ค่าที่เห็นอาจเป็น last-good และจะไม่ถูกปลอมเป็น 100% |

สีเหลืองหมายถึงเหลือไม่เกิน 20% และสีแดงหมายถึงเหลือไม่เกิน 10%

## แหล่งข้อมูลและลำดับความสำคัญ

### Codex

1. quota จาก live usage API โดยใช้ session ที่ Codex CLI มีอยู่แล้วในเครื่อง — แสดงทั้งหน้าต่าง **5 ชั่วโมง** (`5H`) และรายสัปดาห์ (`W`) พร้อมเวลา reset แยกกันบน taskbar, tray และ dashboard
2. หาก live quota ใช้ไม่ได้ จะคง last-good พร้อมสถานะ error/stale
3. label สรุปมาจาก duration จริงที่ backend รายงาน (เช่น `4H`, `75m`) ไม่ใช่ค่าตายตัว
4. token usage รวมจาก `%USERPROFILE%\.codex\sessions` และ `archived_sessions` โดยอ่าน JSONL แบบ incremental, ตัด event replay ซ้ำ และรองรับทั้ง `last_token_usage` กับ cumulative delta

เลือก window ที่จะแสดงบน taskbar ต่อ provider ได้ผ่าน `display.taskbar_windows` ใน `config.json` (ค่า default = built-in order):

```json
{
  "display": {
    "taskbar_windows": { "codex": ["session", "weekly"] }
  }
}
```

ตั้งเป็น `["weekly"]` เพื่อกลับไปใช้โหมดแสดงเฉพาะโควต้ารายสัปดาห์ id ที่ใช้ได้: `session`, `weekly`, `monthly`, `code_review`, `pro`, `flash`, `flash_lite`

### Antigravity

1. local API ของ AGY ที่กำลังรันอยู่
2. cloud API ของ Google (`fetchAvailableModels`) โดยอ่าน OAuth token ที่ agy CLI เก็บไว้แล้วใน Windows Credential Manager (`gemini:antigravity`) แบบ read-only — ไม่ต้องเปิด AGY และจะ refresh access token เองเมื่อหมดอายุเมื่อกำหนด OAuth client ผ่าน environment variables
3. cache ล่าสุดของ AGY พร้อมอายุข้อมูล
4. last-good ของ tracker พร้อมสถานะที่ตรงกับความจริง

Tracker จะ **ไม่เปิด `agy.exe` เอง** จึงไม่สร้าง MCP process หรือ console popup ตามรอบ refresh ต้องเปิด `agy` CLI และ sign in ให้สำครัญอย่างน้อยหนึ่งครั้งก่อน เพื่อให้ credential ถูกเก็บใน Credential Manager ที่ tracker อ่าน

สำหรับการ refresh token ของ AGY ให้กำหนด `Q_TRACKER_AGY_OAUTH_CLIENT_ID` และ `Q_TRACKER_AGY_OAUTH_CLIENT_SECRET` (เพิ่ม suffix `_2` ได้สำหรับ client สำรอง) ใน environment ของเครื่อง โดยไม่เก็บค่าไว้ใน repository

### Gemini

1. อ่าน OAuth credentials ที่ Gemini CLI เก็บไว้แล้วในเครื่อง (`~\.gemini\oauth_creds.json` หรือ encrypted `~\.gemini\mcp-oauth-tokens-v2.json`)
2. ต่อ API ของ Code Assist backend (`cloudcode-pa.googleapis.com`) เพื่อขอ quota ราย model family (`pro`, `flash`, `flash_lite`) และ refresh token เมื่อกำหนด OAuth client ผ่าน environment variables
3. ต้องรัน `gemini` CLI และ sign in ให้สำเร็จอย่างน้อยหนึ่งครั้งก่อน Q-Tracker จะเห็น credential และแสดง quota ได้

Tracker จะ **ไม่สปอน `gemini` CLI** ไม่สร้าง child process หรือ console popup โดยอ่านและ refresh token ที่มีอยู่แล้วผ่าน OAuth ตามวิธีเดียวกับเครื่องมือ open-source อื่น (gusage, TokenTracker)

สำหรับการ refresh token ของ Gemini ให้กำหนด `Q_TRACKER_GEMINI_OAUTH_CLIENT_ID` และ `Q_TRACKER_GEMINI_OAUTH_CLIENT_SECRET` ใน environment ของเครื่อง โดยไม่เก็บค่าไว้ใน repository

## Build เป็น executable

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Build gate จะรัน test suite ก่อนสร้าง PyInstaller แบบ `onedir/windowed` ที่:

```text
dist\Q-Tracker\Q-Tracker.exe
```

ต้องเก็บทั้งโฟลเดอร์ `dist\Q-Tracker` ไว้ด้วยกัน ไม่ควรคัดลอกเฉพาะไฟล์ `.exe`

## คำสั่งดูแลระบบ

```powershell
# บังคับ refresh และแสดงสถานะ provider
.\.venv\Scripts\python.exe .\app.py --refresh

# health report ที่ whitelist field และตัดข้อมูลลับออก
.\.venv\Scripts\python.exe .\app.py --diagnostics

# เปิด dashboard โดยตรง
.\.venv\Scripts\pythonw.exe .\app.py --dashboard

# รันชุดทดสอบทั้งหมด
.\.venv\Scripts\python.exe -m pytest -q
```

## ตำแหน่งข้อมูล

| โหมด | State/config/log |
|---|---|
| รันจาก source | `<project>\data` |
| รัน executable | `%LOCALAPPDATA%\Q-Tracker` |

ไฟล์หลักคือ `token_usage.json`, `config.json` และ `logs\app.log` State ใช้ schema v3, file lock ข้าม process และ atomic replace; หาก JSON เสียจะสำรองเป็น `token_usage.json.corrupt-*` ก่อนกู้ค่าเริ่มต้น

## Privacy และความปลอดภัย

- ไม่มี telemetry, cloud sync, browser-cookie extraction หรือ billing estimate
- ไม่บันทึก access token, refresh token, Authorization header, CSRF token หรือ response body ลง log/diagnostics
- Codex auth ถูกอ่านเฉพาะเพื่อ request quota ที่ผู้ใช้มี session อยู่แล้ว
- AGY ติดต่อเฉพาะ local API/process ที่กำลังรัน, อ่าน OAuth token จาก Windows Credential Manager แบบ read-only เพื่อขอ quota จาก Google โดยตรง และอ่าน cache ในเครื่อง (ไม่มีการเขียน token กลับ)
- Gemini ใช้เฉพาะ OAuth token ที่ CLI เก็บไว้ในเครื่อง ต่อกับ API อย่างเป็นทางการของ Google และไม่เคยส่ง token ไปที่อื่น
- Diagnostics แสดงเฉพาะ boolean, version, path ที่ย่อชื่อ user, timestamp, source/status และจำนวนไฟล์สแกน

## แก้ปัญหาเบื้องต้น

**Codex ขึ้น `auth_required` หรือ `unavailable`**

เปิด Codex CLI และ sign in ให้สำเร็จ แล้วกด `Refresh now` Token totals จาก session logs ยังทำงานได้แม้ live quota ใช้ไม่ได้

**AGY ขึ้น stale/unavailable**

ต้องมี sign in ผ่าน `agy` CLI อย่างน้อยหนึ่งครั้งเพื่อให้เก็บ OAuth token ไว้ใน Windows Credential Manager หลังจากนั้น tracker ดึง quota ผ่าน cloud API ได้แม้ AGY ปิดอยู่ หากยังไม่ขึ้นให้เปิด Antigravity ตามปกติและกด `Refresh now` Tracker จะไม่เปิด AGY แทนผู้ใช้

**Gemini ขึ้น `auth_required` หรือ `unavailable`**

เปิด `gemini` CLI และ sign in ให้สำเร็จครั้งเดียว (Gemini CLI จะเขียน credential ลง `~\.gemini`) แล้วกด `Refresh now`

**taskbar ไม่ปรากฏหลัง Explorer restart/เปลี่ยนจอ**

Exit จาก tray แล้วเปิด `run.bat` ใหม่ ตัว widget รองรับ display/DPI/taskbar setting change และคำนวณตำแหน่งจาก client area ของ taskbar ใหม่อัตโนมัติ โดยอ่านขอบเขต notification area แบบ dynamic และเว้นพื้นที่อย่างน้อยหนึ่งช่องตามความสูง taskbar เพื่อไม่บังลูกศรหรือไอคอน รวมทั้งใช้ตำแหน่งสำรองชั่วคราวระหว่าง Explorer จัด layout ใหม่

**การแสดงผลเมื่อเปิดหน้าต่างเต็มจอ**

Q-Tracker เป็น child window ภายใน `Shell_TrayWnd` ตามรูปแบบ taskbar-integrated widget จึงคงตำแหน่งเมื่อ taskbar จัด layout ใหม่และยังแสดงเมื่อเปิด Start หรือคลิก Desktop/Maximize ตามปกติ แต่จะซ่อนอัตโนมัติเมื่อ taskbar ไม่พร้อมใช้งาน หรือเมื่อเกม/วิดีโอด้านหน้าใช้ fullscreen/borderless fullscreen บนจอเดียวกัน และจะแสดงกลับโดยไม่แย่ง focus

**ต้องการตรวจสุขภาพโดยไม่เปิด console ค้าง**

เปิด dashboard แล้วกด `Diagnostics` หรือรัน `app.py --diagnostics` จาก PowerShell Log หมุนอัตโนมัติที่ 1 MB จำนวน 3 ไฟล์สำรอง

**ติดตั้งไม่สำเร็จ**

ตรวจว่า `winget` ใช้งานได้ แล้วรัน `setup.ps1` ใหม่ การติดตั้งทั้งหมดเป็น per-user

## ข้อจำกัดที่ตั้งใจไว้

เวอร์ชันนี้รองรับ Windows 11, Codex, Antigravity และ Gemini CLI เท่านั้น ไม่ดึง billing, ไม่คาดเดา quota ที่ provider ไม่ส่งมา และไม่เปิด provider process เพื่อบังคับ refresh
