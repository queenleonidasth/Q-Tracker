# Q-Tracker for Windows

แอป Windows 11 สำหรับดู quota ของ **Codex** และ **Antigravity (AGY)** บน taskbar พร้อม system tray, dashboard, การรวม token usage ของ Codex อัตโนมัติ และการแจ้งเตือนเมื่อ quota ต่ำ

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

1. quota จาก live usage API โดยใช้ session ที่ Codex CLI มีอยู่แล้วในเครื่อง
2. หาก live quota ใช้ไม่ได้ จะคง last-good พร้อมสถานะ error/stale
3. token usage รวมจาก `%USERPROFILE%\.codex\sessions` และ `archived_sessions` โดยอ่าน JSONL แบบ incremental, ตัด event replay ซ้ำ และรองรับทั้ง `last_token_usage` กับ cumulative delta

### Antigravity

1. local API ของ AGY ที่กำลังรันอยู่
2. cache ล่าสุดของ AGY พร้อมอายุข้อมูล
3. last-good ของ tracker พร้อมสถานะที่ตรงกับความจริง

Tracker จะ **ไม่เปิด `agy.exe` เอง** จึงไม่สร้าง MCP process หรือ console popup ตามรอบ refresh หาก AGY ไม่ได้รันอยู่ สถานะจะเป็น stale/unavailable ตาม cache ที่มี

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
- AGY ติดต่อเฉพาะ local API/process ที่กำลังรันและอ่าน cache ในเครื่อง
- Diagnostics แสดงเฉพาะ boolean, version, path ที่ย่อชื่อ user, timestamp, source/status และจำนวนไฟล์สแกน

## แก้ปัญหาเบื้องต้น

**Codex ขึ้น `auth_required` หรือ `unavailable`**

เปิด Codex CLI และ sign in ให้สำเร็จ แล้วกด `Refresh now` Token totals จาก session logs ยังทำงานได้แม้ live quota ใช้ไม่ได้

**AGY ขึ้น stale/unavailable**

เปิด Antigravity ตามปกติและตรวจว่า local service ของ AGY รันอยู่ Tracker จะไม่เปิด AGY แทนผู้ใช้

**taskbar ไม่ปรากฏหลัง Explorer restart/เปลี่ยนจอ**

Exit จาก tray แล้วเปิด `run.bat` ใหม่ ตัว widget รองรับ display/DPI/taskbar setting change และคำนวณตำแหน่งจาก client area ของ taskbar ใหม่อัตโนมัติ โดยอ่านขอบเขต notification area แบบ dynamic เพื่อไม่บังลูกศรหรือไอคอน และใช้ตำแหน่งสำรองชั่วคราวระหว่าง Explorer จัด layout ใหม่

**การแสดงผลเมื่อเปิดหน้าต่างเต็มจอ**

Q-Tracker เป็น child window ภายใน `Shell_TrayWnd` ตามรูปแบบ taskbar-integrated widget จึงคงตำแหน่งเมื่อ taskbar จัด layout ใหม่และยังแสดงเมื่อเปิด Start หรือคลิก Desktop/Maximize ตามปกติ แต่จะซ่อนอัตโนมัติเมื่อ taskbar ไม่พร้อมใช้งาน หรือเมื่อเกม/วิดีโอด้านหน้าใช้ fullscreen/borderless fullscreen บนจอเดียวกัน และจะแสดงกลับโดยไม่แย่ง focus

**ต้องการตรวจสุขภาพโดยไม่เปิด console ค้าง**

เปิด dashboard แล้วกด `Diagnostics` หรือรัน `app.py --diagnostics` จาก PowerShell Log หมุนอัตโนมัติที่ 1 MB จำนวน 3 ไฟล์สำรอง

**ติดตั้งไม่สำเร็จ**

ตรวจว่า `winget` ใช้งานได้ แล้วรัน `setup.ps1` ใหม่ การติดตั้งทั้งหมดเป็น per-user

## ข้อจำกัดที่ตั้งใจไว้

เวอร์ชันนี้รองรับ Windows 11, Codex และ Antigravity เท่านั้น ไม่ดึง billing, ไม่คาดเดา quota ที่ provider ไม่ส่งมา และไม่เปิด provider process เพื่อบังคับ refresh
