# opcode — Q-Tracker session log

สรุปงานที่ทำใน session นี้: เพิ่ม Gemini quota provider ให้ Q-Tracker (Windows 11 quota tracker
สำหรับ Codex / Antigravity / Gemini) โดยไม่สปอน CLI ใดๆ

- วันที่: 2026-08-18
- โปรเจกต์: `C:\Users\QUEEN\Q-Tracker`
- จุด roll back: git tag `before-gemini-provider` @ commit `598f15b`

---

## 1. เป้าหมาย

- Track quota ของ Gemini CLI (เช่นเดียวกับ Codex/AGY) บน taskbar / tray / dashboard
- **ห้าม**เปิด/สปอน CLI (ห้าม child process, ห้าม console popup) ตามนโยบายเดียวกับ AGY
- stdlib-only (Python 3.13/3.14), privacy-first
- มี roll back ไว้ก่อนเริ่มแก้ (ผู้ใช้สั่งทำ roll back ก่อน)

## 2. วิธีที่ค้นคว้า (reference repos)

Clone ไว้ที่ `C:\Users\QUEEN\AppData\Local\Temp\opencode\`:

- **gusage** (`a-hariti/gusage`) → `gusage\index.ts` — decrypt v2 token store + API flow
- **TokenTracker** (`xiufengsun/TokenTracker`) → `TokenTracker\src\lib\usage-limits.js`
  (บรรทัด ~895–1260, OAuth client fallback ~961–990)

หลักการร่วมกัน:

1. อ่าน OAuth credentials ที่ Gemini CLI เขียนไว้แล้วใน `~\.gemini`
2. ถ้า token หมดอายุ → refresh ที่ `https://oauth2.googleapis.com/token`
3. POST `https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist`
   → ได้ `cloudaicompanionProject.projectId`
4. POST `...:retrieveUserQuota` → ได้ `buckets` (per-model quota)
5. ไม่มี process/CLI เกิดใหม่เลย

## 3. ข้อเท็จจริงสำคัญที่พบ

### Credential 2 รูปแบบใน `~\.gemini`
- Legacy: `oauth_creds.json` — JSON plain text (`access_token`, `refresh_token`, `expiry_date`)
- V2 encrypted: `mcp-oauth-tokens-v2.json` — รูปแบบ `ivHex:authTagHex:ciphertextHex`
  - AES-256-GCM
  - key = scrypt(pw=`b"gemini-cli-oauth"`, salt=`"{hostname}-{username}-gemini-cli"`,
    n=2**14, r=8, p=1, dklen=32)
  - โครงสร้าง JSON: `{"main-account": {"token": {"accessToken", "refreshToken", "expiresAt", ...}}}`
  - `MAIN_ACCOUNT_KEY = "main-account"`

### OAuth client configuration
- ค่า OAuth client สำหรับ Gemini อ่านจาก `Q_TRACKER_GEMINI_OAUTH_CLIENT_ID` และ `Q_TRACKER_GEMINI_OAUTH_CLIENT_SECRET`
- ค่า OAuth client สำหรับ AGY อ่านจาก `Q_TRACKER_AGY_OAUTH_CLIENT_ID` และ `Q_TRACKER_AGY_OAUTH_CLIENT_SECRET` (เพิ่ม suffix `_2` ได้)
- ค่า credential ไม่ถูกเก็บไว้ในโค้ดหรือ repository

### Endpoint / ค่าคงที่
- `CODE_ASSIST_ENDPOINT = https://cloudcode-pa.googleapis.com/v1internal`
- `TOKEN_URL = https://oauth2.googleapis.com/token`
- `REQUEST_TIMEOUT = 8` วินาที

### สภาพเครื่องผู้ใช้ (ตรวจแล้ว)
- `~\.gemini` มีแค่ `antigravity-cli` + `config` → **ไม่มี** credential ของ Gemini CLI
- ไม่มี `gemini` binary ใน PATH
- AGY cache ที่ `~\.tokentracker\tracker\agy_quota_cache.json` มี `gemini-5h` 78.6% /
  `gemini-weekly` 86.2% (เป็น quota ฝั่ง AGY ไม่ใช่ Gemini CLI)
- → ผลลัพธ์: provider ใหม่จะแสดง `auth_required`/`unavailable` จนกว่าจะรัน `gemini` login ครั้งเดียว
  (ยอมรับได้ ตรงกับ pattern ของ Codex)

## 4. ไฟล์ที่แก้/สร้าง

### สร้างใหม่
- `gemini_api_client.py` — ไฟล์หลักของ feature (775 บรรทัด)
  - AES-256-GCM decrypt แบบ pure stdlib (NIST FIPS-197 + SP 800-38D) เพื่ออ่าน v2 token store
  - อ่าน credential legacy + encrypted
  - refresh access token
  - `loadCodeAssist` + `retrieveUserQuota`
  - normalize เป็น `{plan_type, timestamp, models: {pro|flash|flash_lite: {used_percent,
    percent_left, remaining_fraction, resets_at, model_id}}}`
  - `main()` โหมด CLI test (print สถานะ ไม่เปิด app)
- `tests/test_gemini_api_client.py` — 11 tests

### แก้ไข
- `quota_sources.py` — เพิ่ม `GeminiQuotaSource` (map models → `QuotaWindow`, failure →
  `ProviderSnapshot.failure` แบบเดียวกับ Codex/AGY)
- `usage_service.py` — register `"gemini": GeminiQuotaSource()` ใน `get_service()` (เดิมบรรทัด ~341)
- `settings.py` — เพิ่ม gemini ใน `DEFAULT_PROVIDER_STYLES` + เปิดให้ `to_dict()` map ชื่อ "Gemini"
- `ui_models.py` — `provider_order` default = `("agy", "codex", "gemini")`,
  `taskbar_windows()` แสดง pro/flash/flash_lite, `_window_order` + `_short_label` เพิ่มครอบครัว Gemini
- `tests/test_provider_sources.py` — +4 tests ของ GeminiQuotaSource
- `tests/test_settings_and_models.py` — อัปเดต default `enabled_providers`
- `data/config.json` — เพิ่ม `gemini` ใน `enabled_providers` (ไฟล์นี้ gitignored)
- `README.md` — เอกสาร Gemini (วิธี auth ครั้งเดียว, แหล่งข้อมูล, privacy, แก้ปัญหา)

### Mapping หน้าต่าง quota
- ต่อ **model family** ไม่ใช่ session/weekly (ต่างจาก Codex/AGY): `pro` / `flash` / `flash_lite`
- ในครอบครัวเดียวกัน ถ้ามีหลาย model variant → ใช้ค่า remaining fraction **ต่ำสุด** (คล้าย TokenTracker)
- `VALID_GEMINI_MODELS`: gemini-3.1-pro-preview, gemini-3-flash-preview, gemini-2.5-pro,
  gemini-2.5-flash, gemini-2.5-flash-lite

## 5. บั๊ก crypto ที่เจอและแก้ (สำคัญ)

ตอน validate AES-256-GCM กับ known-answer vector พบ 3 บั๊ก:

1. **`_aes_round_keys` เอา key เป็น list คำ 60 คำ ไม่ได้จัดกลุ่ม**
   → แก้: `[sum(words[i*4:i*4+4], []) for i in range(15)]` ให้เป็น 15 round keys (16 ไบต์)
2. **`_gf_multiply` วนบิต LSB-first ผิดสเปก GHASH**
   → แก้: วน `range(127, -1, -1)` (MSB-first) ตาม NIST SP 800-38D
3. **GCM เริ่ม counter ที่ `J0` ผิด** — สเปกกำหนดให้ encryption เริ่มที่ `inc32(J0)`
   (tag ยังใช้ `CIPH_K(J0)` ตามเดิม)
   → แก้: `current = counter_block(j0)` ใน `_aes_gcm_decrypt`

### การ validate
- AES-256 block cipher ผ่าน FIPS-197 known-answer
  (`001122...ff` → `8ea2b7ca516745bfeafc49904b496089`)
- GCM decrypt ผ่าน 3 vectors + tamper-reject โดย cross-check กับ **.NET `AesGcm`**
  (validate .NET เองกับ NIST GCM case 16 ก่อน → ได้ CT `0388dace60b6a392f328c2b971b2fe78`
  / tag `ab6e47d42cec13bdf53a67b21257bddf` ตรง)
- NIST vector ที่จำเองสำหรับ AES-256-GCM กลายเป็นไม่ตรง (จำผิด) → ใช้ค่าจาก .NET
  เป็น ground truth: key `feffe9...`, iv `000102030405060708090a0b`,
  pt `001122...ff` → CT `fc82f254a110ffcedba122934be12bed`, tag `2543ad0b91fe9b2ef060a9a5a46c52a4`

## 6. สถานะการทดสอบ

- Baseline ก่อนแก้: `152 passed in 0.94s`
- หลังแก้: **`166 passed`** (+14 tests: 11 client + 3 provider source + 1 settings ที่อัปเดต)
- Smoke test: `GeminiQuotaSource().fetch()` → `FetchStatus.UNAVAILABLE / auth_required`
  → render เป็น `Gemini ! —` ถูกต้อง (ไม่ปลอมเป็น 100%)
- `python gemini_api_client.py` → พิมพ์ "No Gemini credentials found. Run `gemini` and sign in once"

## 7. Roll back

```powershell
git reset --hard before-gemini-provider
```

- tag `before-gemini-provider` สร้างก่อนเริ่มแก้อะไร ที่ commit `598f15b56f89b750aaf28640ab9fd239788f2587`
- working tree ตอนนั้นสะอาด, อยู่บน main, ahead origin/main 7 commits
- `data/config.json` เป็น runtime (gitignored) → roll back แบบ hard reset ไม่กู้คืนให้ ต้องตั้งเอง

## 8. TODO / ขั้นต่อไป

- [x] ตรวจ AES-256-GCM กับ known-answer vector
- [x] เพิ่ม `GeminiQuotaSource` ใน quota_sources.py
- [x] register ใน usage_service.py `get_service()`
- [x] settings.py `DEFAULT_PROVIDER_STYLES` + `to_dict`
- [x] ui_models.py (provider_order / taskbar_windows / _window_order / _short_label)
- [x] tests + รันทั้ง suite (166 ผ่าน)
- [x] README + data/config.json
- [ ] **ทดสอบ end-to-end จริง** — รอผู้ใช้รัน `gemini` CLI แล้ว sign in ครั้งเดียว
      (ตอนนี้เครื่องยังไม่มี credential → ยังยืนยัน flow จริงไม่ได้)
- [ ] (ยังไม่ทำ) commit การเปลี่ยนแปลง

## 9. ไฟล์อ้างอิง / ตำแหน่ง

| ไฟล์ | บทบาท |
|---|---|
| `C:\Users\QUEEN\Q-Tracker\gemini_api_client.py` | ไฟล์ใหม่: client หลัก (AES-GCM, OAuth, quota API) |
| `C:\Users\QUEEN\Q-Tracker\quota_sources.py` | `GeminiQuotaSource` (บรรทัด ~391) |
| `C:\Users\QUEEN\Q-Tracker\usage_service.py` | register provider (บรรทัด ~344) |
| `C:\Users\QUEEN\Q-Tracker\settings.py` | style + to_dict mapping (บรรทัด ~16, ~137) |
| `C:\Users\QUEEN\Q-Tracker\ui_models.py` | window order/short label/taskbar/provider_order |
| `C:\Users\QUEEN\Q-Tracker\tests\test_gemini_api_client.py` | 11 tests client |
| `C:\Users\QUEEN\Q-Tracker\tests\test_provider_sources.py` | +4 tests GeminiQuotaSource |
| `C:\Users\QUEEN\Q-Tracker\data\config.json` | runtime config (gitignored) |
| `~\.gemini` | ตำแหน่ง credential ของ Gemini CLI (ต้อง login ครั้งเดียว) |
| `C:\Users\QUEEN\AppData\Local\Temp\opencode\gusage\index.ts` | reference (decrypt v2 + API) |
| `C:\Users\QUEEN\AppData\Local\Temp\opencode\TokenTracker\src\lib\usage-limits.js` | reference (normalize/OAuth) |
