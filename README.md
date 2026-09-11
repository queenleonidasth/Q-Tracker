# Q-Tracker for Windows

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows%2011-blue?logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/Python-3.13%2B-blue?logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License" />
  <a href="https://github.com/queenleonidasth/Q-Tracker/releases"><img src="https://img.shields.io/github/v/release/queenleonidasth/Q-Tracker?color=orange&label=Release" alt="GitHub Release" /></a>
</p>

แอปพลิเคชันสำหรับ Windows 11 ที่แสดงสถานะโควต้า (Quota) และการใช้งานโทเค็นของ AI Coding Assistants ยอดนิยม ได้แก่ **Codex**, **Antigravity (AGY)** และ **Gemini CLI** โดยฝังลงบน **Windows Taskbar** แบบเนียนตา พร้อมเมนู System Tray, หน้าต่าง Dashboard ที่ทันสมัย, ระบบรวบรวม Token อัตโนมัติ และการแจ้งเตือนเมื่อโควต้าใกล้หมด

🔒 **Privacy & Performance First**: ข้อมูลทั้งหมดถูกประมวลผลและเก็บอยู่ภายในเครื่องของคุณ ไม่มีการส่ง telemetry ใดๆ ออกไปภายนอก ไม่เก็บ access token ลง state/log และไม่เปิด process CLI/หน้าต่าง console รบกวนการทำงาน

---

## 🌟 จุดเด่นหลัก (Key Features)

- 📌 **Taskbar Integration**: แสดงโควต้าและเวลา countdown จน reset ถัดไปบน Taskbar ของ Windows 11 โดยตรง รองรับทั้ง Taskbar จัดกึ่งกลาง (Center) และจัดชิดซ้าย (Left-aligned) โดยคำนวณตำแหน่งหลบปุ่ม Start และ Task buttons อัตโนมัติ ไม่บัง System Tray
- 🔄 **Explorer Auto-Reconnect**: ทนทานต่อการ restart หรือ crash ของ Windows Explorer ด้วย thread-level timer ที่จะ attach กลับเข้า taskbar ใหม่ทันทีโดยไม่ต้องเปิดแอปใหม่
- ⚡ **Multi-Provider Quota Tracking**:
  - **Codex**: แสดงทั้งหน้าต่างสั้น **5 ชั่วโมง (`5H`)** และรายสัปดาห์ (`W`) พร้อมเวลา reset แยกกัน และแปลง label ตาม duration จริง (เช่น `4H`, `75m`) รวบรวม token usage อัตโนมัติจาก session log แบบ incremental
  - **Antigravity (AGY)**: ดึง quota จาก local API ที่กำลังรัน หรือดึงผ่าน Google Cloud API (`fetchAvailableModels`) โดยอ่าน OAuth credential จาก Windows Credential Manager (`gemini:antigravity`) แบบ read-only โดยไม่ต้องเปิดโปรแกรม AGY ทิ้งไว้
  - **Gemini CLI**: ดึง quota สำหรับโมเดล Pro, Flash, Flash Lite ผ่าน Code Assist API (`cloudcode-pa.googleapis.com`) โดยอ่าน credential ที่มีอยู่แล้วจาก `~/.gemini`
- 🛡️ **Zero Console / Zero Child Spawn**: ดึงและ refresh token ผ่าน OAuth และ local APIs โดยตรง ไม่สั่งรัน child process หรือเปิด popup console
- 🔔 **Smart Notifications**: แจ้งเตือนผ่าน Windows notification เมื่อโควต้าลดลงเหลือ 20%, 10% และ 5% (แจ้งครั้งเดียวต่อ threshold ในแต่ละ window)
- 📊 **Rich Dashboard**: ดับเบิลคลิกเพื่อดูรายละเอียดแบบเจาะลึก แยกตาม provider, ดู source, freshness, สถานะ connection, และสถิติ token วันนี้ / เดือนนี้ / ทั้งหมด
- 🚀 **Auto-Startup**: สามารถเปิดให้รันพร้อม Windows ได้ในคลิกเดียวผ่าน Dashboard (เขียนเฉพาะ registry HKCU ไม่ต้องใช้สิทธิ์ Admin)

---

## 📥 ดาวน์โหลดและติดตั้ง

### วิธีที่ 1: ดาวน์โหลด Release (แนะนำสำหรับผู้ใช้ทั่วไป)
1. ไปที่หน้า [Releases](https://github.com/queenleonidasth/Q-Tracker/releases/latest)
2. ดาวน์โหลดไฟล์ `Q-Tracker-v1.4.0-windows-x64.zip`
3. แตกไฟล์ zip ไปยังโฟลเดอร์ที่ต้องการ (เช่น `C:\Users\<user>\AppData\Local\Programs\Q-Tracker` หรือโฟลเดอร์ใดๆ)
4. ดับเบิลคลิกไฟล์ `Q-Tracker.exe` เพื่อเริ่มใช้งานได้ทันที

### วิธีที่ 2: ติดตั้งและรันจาก Source Code (สำหรับ Developers)
เปิด PowerShell ในโฟลเดอร์โปรเจกต์ แล้วรันสคริปต์ setup ครั้งเดียว:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

สคริปต์จะค้นหาหรือติดตั้ง Python 3.13 (แบบ per-user ผ่าน winget ถ้าจำเป็น), สร้าง `.venv`, ติดตั้ง dependencies และรันชุดการทดสอบทั้งหมด (Tests)

จากนั้นสามารถเปิดใช้งานได้ผ่าน:

```powershell
.\run.bat
```

> `run.bat` จะเลือกไฟล์ executable ในโฟลเดอร์ `dist` ก่อน หากยังไม่ได้ build จะรันผ่าน `.venv\Scripts\pythonw.exe` ทำให้ไม่มีหน้าต่าง console สีดำค้างไว้

---

## 🖥️ การใช้งาน

- **ดับเบิลคลิก** ข้อความบน Taskbar หรือ Tray Icon: เพื่อเปิดหน้าต่าง Dashboard
- **คลิกขวา** ที่ข้อความบน Taskbar หรือ Tray Icon: เพื่อเปิดเมนูลัด (Refresh, Token Summary, Exit)
- **สัญลักษณ์สถานะบน Taskbar**:
  | สถานะ | เครื่องหมาย | ความหมาย |
  |---|:---:|---|
  | `ok` | *(ไม่มี)* | ข้อมูลถูกต้องและอัปเดตจาก source ล่าสุด |
  | `stale` | `~` | แสดงค่าเดิมที่ยังมีประโยชน์ แต่ source ยังไม่ยืนยันข้อมูลรอบใหม่ |
  | `error` / `unavailable` | `!` | พบปัญหาในการ refresh โดยค่าที่เห็นจะเป็น last-good และจะไม่ถูกปลอมเป็น 100% |
- **สีสถานะ**: 
  - สีปกติ (ตามสี provider): โควต้าคงเหลือ > 20%
  - 🟡 **สีเหลือง**: โควต้าเหลือน้อยกว่าหรือเท่ากับ 20%
  - 🔴 **สีแดง**: โควต้าเหลือน้อยกว่าหรือเท่ากับ 10%

---

## ⚙️ แหล่งข้อมูลและการตั้งค่า (Data Sources & Configuration)

### 1. Codex
- ดึง quota จาก live usage API ผ่าน local session ที่ Codex CLI มีอยู่แล้วในเครื่อง
- แสดงหน้าต่างรอบระยะสั้น (`5H`) และระยะยาว (`W`) พร้อม countdown เวลา reset
- สแกนและรวบรวม token จาก `%USERPROFILE%\.codex\sessions` และ `archived_sessions`

### 2. Antigravity (AGY)
- อ่านจาก local API ของโปรแกรม AGY ที่กำลังทำงาน
- หาก AGY ไม่ได้เปิดอยู่ จะ fallback ไปดึงผ่าน Google Cloud API โดยอ่าน OAuth credential จาก Windows Credential Manager (`gemini:antigravity`) แบบ read-only
- ต้องเคย sign in ผ่าน `agy` CLI ในเครื่องอย่างน้อย 1 ครั้ง
- รองรับการตั้งค่า Environment Variables สำหรับ refresh token:
  - `Q_TRACKER_AGY_OAUTH_CLIENT_ID`
  - `Q_TRACKER_AGY_OAUTH_CLIENT_SECRET` (และ suffix `_2` สำหรับ secondary client)

### 3. Gemini CLI
- อ่าน OAuth credential ที่ Gemini CLI จัดเก็บไว้ใน `~/.gemini/oauth_creds.json` หรือ encrypted `~/.gemini/mcp-oauth-tokens-v2.json`
- เชื่อมต่อ Code Assist backend (`cloudcode-pa.googleapis.com`) เพื่อดึง quota รายโมเดล (`pro`, `flash`, `flash_lite`)
- รองรับการตั้งค่า Environment Variables สำหรับ refresh token:
  - `Q_TRACKER_GEMINI_OAUTH_CLIENT_ID`
  - `Q_TRACKER_GEMINI_OAUTH_CLIENT_SECRET`

### การปรับแต่ง `config.json`
ไฟล์คอนฟิกถูกเก็บอยู่ที่ `data/config.json` (เมื่อรันจาก source) หรือ `%LOCALAPPDATA%\Q-Tracker\config.json` (เมื่อรัน executable):

```json
{
  "refresh_interval_seconds": 60,
  "enabled_providers": ["agy", "codex"],
  "notification_thresholds": [0.2, 0.1, 0.05],
  "display": {
    "taskbar_windows": {
      "codex": ["session", "weekly"]
    }
  }
}
```

- `display.taskbar_windows`: เลือก window ที่จะให้แสดงบน taskbar สำหรับ provider นั้นๆ เช่น `["session", "weekly"]` หรือ `["weekly"]` (ค่าที่รองรับ: `session`, `weekly`, `monthly`, `code_review`, `pro`, `flash`, `flash_lite`)

---

## 🔨 การ Build เป็น Executable

สามารถคอมไพล์โปรเจกต์เป็น Standalone Windows Executable ได้ง่ายๆ ด้วยคำสั่ง:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

สคริปต์จะทำการ:
1. รัน pytest ตรวจสอบความถูกต้องของโค้ดทั้งหมด
2. ล้างโฟลเดอร์ build เก่า
3. รัน PyInstaller (`Q-Tracker.spec`) สร้าง binary ไว้ที่ `dist\Q-Tracker\`
4. อัปเดตการลงทะเบียน Windows startup (ถ้ามี) ให้ชี้ไปยัง exe ตัวใหม่

---

## 🧰 คำสั่งจัดการและ Debug (CLI Tools)

```powershell
# บังคับ Refresh ข้อมูลทุก Provider ทันที พร้อมแสดงสถานะ
.\.venv\Scripts\python.exe .\app.py --refresh

# แสดงรายงานสถานะระบบ (Diagnostics) โดยจะตัดข้อมูลความลับออกทั้งหมด
.\.venv\Scripts\python.exe .\app.py --diagnostics

# เปิดหน้าต่าง Dashboard โดยตรง
.\.venv\Scripts\pythonw.exe .\app.py --dashboard

# รันชุดทดสอบ (Pytest Suite)
.\.venv\Scripts\python.exe -m pytest -v
```

---

## 🛡️ Privacy, Security & Data Safety

- **No Telemetry**: ไม่มีระบบติดตาม พฤติกรรม หรือส่งข้อมูลสถิติออกภายนอก
- **No Secret Leakage**: ไม่มีการบันทึก access token, refresh token, CSRF token, หรือ Authorization header ลงในไฟล์ log หรือ diagnostics รายงาน diagnostics จะแสดงเฉพาะ boolean, sanitized path และ timestamp เท่านั้น
- **Safe Storage**: บันทึกข้อมูลแบบ Atomic Replacement พร้อม File Locking ข้าม process หากไฟล์ JSON เสียหาย ระบบจะทำสำเนา backup อัตโนมัติก่อนกู้คืนค่าเริ่มต้น
- **Read-Only Credentials**: อ่าน credential ของผู้ใช้เพื่อคุยกับ API ทางการเท่านั้น ไม่มีการดัดแปลงหรือเขียนทับ credentials ของ CLI อื่น

---

## 📄 License

โปรเจกต์นี้เผยแพร่ภายใต้สัญญาอนุญาต [MIT License](LICENSE)
