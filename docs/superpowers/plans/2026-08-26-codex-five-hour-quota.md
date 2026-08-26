# Codex 5-Hour Quota Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ให้ Q-Tracker แสดงและติดตามโควต้า Codex 5 ชั่วโมง (session window) ได้ครบทุกพื้นผิว — taskbar overlay แสดงทั้ง `5H` และ `Weekly`, classification ทนต่อ `limit_window_seconds` ที่ไม่ใช่ 18000s เป๊ะ, label ชั่วโมงคำนวณจาก duration จริง และผู้ใช้เลือกลำดับ window บน taskbar ได้

## Current state analysis

สิ่งที่ทำงานอยู่แล้ว (ไม่ต้องแก้):

- `codex_api_client.fetch_codex_live_limits()` ดึง `wham/usage` และแยก primary (18000s = 5h) กับ secondary (604800s = 7d) จาก response จริง
- `quota_sources.CodexQuotaSource._from_live()` สร้าง windows `session` + `weekly` และ fallback `_latest_session_snapshot()` อ่าน `rate_limits` จาก JSONL ได้ทั้งสอง position
- Dashboard, context menu และ notification policy วนทุก window ใน snapshot อยู่แล้ว (dedupe key ต่อ `provider/window/reset/threshold`)

ช่องว่างที่ต้องแก้:

1. **Taskbar overlay แสดง window เดียวและเลือก weekly ก่อน** — `ui_models.taskbar_windows()` (ui_models.py:82-86) วน `("weekly", "session")` แล้ว return ตัวเดียว ทำให้โควต้า 5H ถูกซ่อนเมื่อมี weekly; test `test_taskbar_windows_select_only_codex_weekly` บังคับพฤติกรรมนี้ไว้
2. **Classification แบบ exact match** — `_classify_window()` จับเฉพาะ 18000/604800 เป๊ะ ถ้า backend ส่ง duration ต่างออกไป (เช่น 14400s) จะตกไปใช้ positional fallback และอาจผิด window
3. **Label บังคับเป็น `"5H"`** ทั้งใน `quota_sources` (hard-coded string) และ `ui_models._short_label("session")` ไม่ว่า duration จริงจะกี่นาที
4. **ไม่มีตัวเลือกให้ผู้ใช้เลือก** ว่าจะให้ taskbar แสดง window ไหนบ้าง (เดิม weekly-only หายไปโดยไม่มีทางเลือกคืน)

## Architecture

คง pipeline เดิมทั้งหมด: `CodexQuotaSource` → `UsageService` (TTL/single-flight/monotonic guard) → persisted state → `build_provider_view` → UI surfaces การแก้ทั้งหมดอยู่ที่ (a) selection logic ใน presentation model `taskbar_windows`, (b) classification/normalize ใน `codex_api_client`, (c) label derivation ใน `quota_sources`, (d) setting key ใหม่ใน `Settings.display`

**Tech Stack:** Python 3.13 stdlib, pytest (ไม่เพิ่ม dependency)

## Global Constraints

- ห้าม log/copy access token หรือ response body ต่อ (privacy policy เดิม)
- Last-good/error semantics เดิมห้ามเปลี่ยน — แก้เฉพาะการเลือกแสดงผลและ classification
- Taskbar ห้ามล้น: ทุกกรณีต้อง degrade ด้วย ladder เดิม (full → compact countdown → ตัด segment ท้าย) ก่อน overflow
- Success TTL 60s / failure TTL 45s / rate-limit TTL 300s คงเดิม
- ทุก task ต้องรัน `.venv\Scripts\python.exe -m pytest -q` ผ่านก่อน commit

---

## File map

- Modify `ui_models.py`: `taskbar_windows()` แสดงทั้ง session+weekly สำหรับ Codex, รับ preferred ids, `_short_label` ใช้ label จาก source
- Modify `codex_api_client.py`: `_classify_window()` fallback ตามช่วง duration
- Modify `quota_sources.py`: label ของ session window คำนวณจาก `window_minutes`
- Modify `settings.py`: validate `display.taskbar_windows`
- Modify `taskbar_widget.py`: ส่ง preferred ids เข้า `taskbar_windows()`
- Update `README.md`: เอกสารพฤติกรรม 5H + Weekly และ config ใหม่
- Tests: `tests/test_ui_models.py`, `tests/test_codex_api_client.py`, `tests/test_provider_sources.py`, `tests/test_settings_and_models.py`, `tests/test_taskbar_widget.py`

---

### Task 1: Taskbar overlay แสดงทั้งโควต้า 5 ชั่วโมงและ Weekly

**Files:**
- Modify: `ui_models.py`
- Test: `tests/test_ui_models.py`

**Interfaces:**
- Produces: `taskbar_windows(provider) -> tuple[WindowView, ...]` ที่คืน **ทั้งสอง** window เรียง `session` ก่อน `weekly` สำหรับ Codex

- [ ] **Step 1: Write failing selection tests**

แก้ `test_taskbar_windows_select_only_codex_weekly` เดิมและเพิ่ม case:

```python
def test_taskbar_windows_select_codex_session_then_weekly():
    """Codex 5-hour quota must be visible alongside the weekly window."""
    provider = build_provider_view(
        _provider(windows={"session": _window("5H", 90), "weekly": _window("Weekly", 80)}),
        NOW,
    )
    assert [w.window_id for w in taskbar_windows(provider)] == ["session", "weekly"]
    assert [w.short_label for w in taskbar_windows(provider)] == ["5H", "W"]

def test_taskbar_windows_select_codex_weekly_when_session_missing():
    provider = build_provider_view(_provider(windows={"weekly": _window("Weekly", 80)}), NOW)
    assert [w.window_id for w in taskbar_windows(provider)] == ["weekly"]
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ui_models.py -q`

Expected: FAIL — `test_taskbar_windows_select_codex_session_then_weekly` ได้ `["weekly"]` ตาม logic เดิม

- [ ] **Step 3: Implement dual-window selection**

แทน branch codex ใน `taskbar_windows()` (ui_models.py:82-86):

```python
if provider.provider_id == "codex":
    selected = tuple(
        by_id[window_id] for window_id in ("session", "weekly") if window_id in by_id
    )
    return selected or provider.windows[:1]
```

- [ ] **Step 4: Run focused tests then full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ui_models.py -q` แล้วตามด้วย `-m pytest -q`

Expected: PASS ทั้งหมด (test fallback เดิม `fall_back_for_codex_without_weekly` ต้องยังผ่าน)

- [ ] **Step 5: Commit**

```powershell
git add ui_models.py tests/test_ui_models.py
git commit -m "feat: show Codex 5-hour and weekly quotas together on taskbar"
```

---

### Task 2: Classification ทนทานและ label ชั่วโมงจาก duration จริง

**Files:**
- Modify: `codex_api_client.py`
- Modify: `quota_sources.py`
- Modify: `ui_models.py`
- Test: `tests/test_codex_api_client.py`, `tests/test_provider_sources.py`, `tests/test_ui_models.py`

**Interfaces:**
- Produces: `_classify_window(window)` คืน `"session"` สำหรับ duration ใด ๆ ที่ `0 < s < 86_400` (exact 18000 ยังจับก่อน), `"weekly"` เมื่อ `s >= 6 * 86_400`
- Produces: session label เป็น `{hours}H` (เช่น `4H`, `5H`) หรือ `{minutes}m` เมื่อไม่ครบชั่วโมง คำนวณจาก `window_minutes`

- [ ] **Step 1: Write failing classification/label tests**

```python
def test_classify_window_accepts_non_standard_session_duration():
    assert codex_api_client._classify_window({"limit_window_seconds": 14_400}) == "session"

def test_normalize_usage_labels_four_hour_window():
    body = {"rate_limit": {"primary_window": {
        "used_percent": 12.0, "limit_window_seconds": 14_400,
        "reset_at": "2026-08-27T00:00:00Z",
    }}}
    result = codex_api_client._normalize_usage_response(body)
    assert result["window_minutes"] == 240
```

```python
def test_codex_source_labels_session_by_duration():
    raw = {"used_percent": 12.0, "percent_left": 88.0, "window_minutes": 240,
           "resets_at": 0, "plan_type": "chatgpt"}
    snapshot = CodexQuotaSource(fetch_live=lambda: raw).fetch()
    assert snapshot.windows["session"].label == "4H"
```

```python
def test_short_label_prefers_hour_label_from_source():
    # _short_label("session", "4H") == "4H"; fallback ยังเป็น "5H" เมื่อ label ว่าง
    assert _short_label("session", "4H") == "4H"
    assert _short_label("session", "") == "5H"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_codex_api_client.py tests/test_provider_sources.py tests/test_ui_models.py -q`

Expected: FAIL — exact-match classify คืน `None` สำหรับ 14400s และ label เป็น `"5H"` ตายตัว

- [ ] **Step 3: Implement range-based classification**

ใน `codex_api_client._classify_window()` ต่อท้าย exact match เดิม:

```python
if 0 < seconds < 86_400:
    return "session"
if seconds >= 6 * 86_400:
    return "weekly"
return None
```

- [ ] **Step 4: Derive session label from minutes**

เพิ่ม helper ใน `quota_sources.py` และใช้แทน string `"5H"` ใน `_from_live()` และ `_latest_session_snapshot()`:

```python
def _window_label(window_id: str, minutes: Optional[int]) -> str:
    if window_id == "weekly":
        return "Weekly"
    if minutes and minutes > 0:
        if minutes % 60 == 0:
            return f"{minutes // 60}H"
        return f"{minutes}m"
    return "5H"
```

- [ ] **Step 5: Prefer source-provided hour label in short_label**

ใน `ui_models._short_label()` branch `session`:

```python
if window_id == "session":
    text = str(label or "").strip().upper()
    if re.fullmatch(r"\d+(H|M)", text):
        return text
    return "5H"
```

(เพิ่ม `import re` ที่ header ไฟล์)

- [ ] **Step 6: Run focused tests then full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_codex_api_client.py tests/test_provider_sources.py tests/test_ui_models.py -q` แล้ว `-m pytest -q`

Expected: PASS — ระวัง assertion เดิมที่ expect `"5H"` กับ fixture 300 นาที (ยังได้ `5H` อยู่ ไม่ควรต้องแก้)

- [ ] **Step 7: Commit**

```powershell
git add codex_api_client.py quota_sources.py ui_models.py tests/
git commit -m "feat: classify Codex windows by duration range and derive hour labels"
```

---

### Task 3: Taskbar layout degrade รองรับสอง segment

**Files:**
- Modify: `tests/test_taskbar_widget.py`
- Modify: `taskbar_widget.py` (เฉพาะเมื่อ test พฤติกรรมจริงแล้วพบ overflow)

**Interfaces:** ไม่เปลี่ยน signature — `_content_width()` วัดผ่าน `_render_segments()` ซึ่งใช้ `taskbar_windows()` ใหม่แล้วโดยอัตโนมัติ และ `_runtime.compact_countdowns` ตัด countdown suffix ก่อนตัด window เสมอ

- [ ] **Step 1: Write layout regression test**

ตาม pattern mock/harness เดิมใน `test_taskbar_widget.py`:

```python
def test_render_segments_include_both_codex_windows():
    view = build_tracker_view({"providers": {"codex": _provider_dict(
        windows={"session": {"remaining_percent": 90, "reset_at": "..."},
                "weekly": {"remaining_percent": 80, "reset_at": "..."}},
    )}})
    segments = taskbar_widget._render_segments(view, {}, include_countdown=True)
    texts = "".join(segment.text for segment in segments)
    assert "5H" in texts and "W" in texts

def test_compact_mode_drops_countdowns_before_dropping_windows():
    # include_countdown=False ต้องยังเห็นทั้ง 5H และ W
    segments = taskbar_widget._render_segments(view, {}, include_countdown=False)
    texts = "".join(segment.text for segment in segments)
    assert "5H" in texts and "W" in texts
```

- [ ] **Step 2: Run focused tests and verify**

Run: `.venv\Scripts\python.exe -m pytest tests/test_taskbar_widget.py -q`

Expected: PASS ด้วย implementation เดิม (width budget: `desired_width = full_needed` ขยาย overlay อัตโนมัติภายใต้ ceiling `MAX_AUTO_WIDTH = 720`) ถ้า FAIL จาก segment ถูกตัด ให้แก้ ladder ใน `_update_layout_path` เท่านั้น ห้าม hardcode ความกว้าง

- [ ] **Step 3: Manual smoke บนเครื่องจริง**

Run: `.\run.bat` แล้วตรวจ taskbar แสดง `Codex <x>% 5H ·<t> · <y>% W ·<t>` ไม่ทับ tray icons

- [ ] **Step 4: Commit**

```powershell
git add taskbar_widget.py tests/test_taskbar_widget.py
git commit -m "test: cover dual Codex window rendering and compact degradation"
```

---

### Task 4: Config เลือก window ที่แสดงบน taskbar

**Files:**
- Modify: `settings.py`
- Modify: `ui_models.py`
- Modify: `taskbar_widget.py`
- Test: `tests/test_settings_and_models.py`, `tests/test_ui_models.py`

**Interfaces:**
- Produces: `Settings.display["taskbar_windows"]: dict[str, tuple[str, ...]]` (default `{}` = ใช้ลำดับ built-in)
- Produces: `taskbar_windows(provider, preferred_ids=None)` — เมื่อระบุ จะวน ids ตามลำดับที่ตั้งและคืนเฉพาะ id ที่มีจริง (คืน `provider.windows[:1]` เมื่อ none matched เพื่อไม่มีวันแสดงว่าง)

- [ ] **Step 1: Write failing settings/model tests**

```python
def test_settings_validate_taskbar_window_map(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"display": {"taskbar_windows": {
        "codex": ["weekly"], "agy": ["bogus"], "gemini": [],
    }}}))
    settings = Settings.load(path)
    assert settings.display["taskbar_windows"] == {"codex": ("weekly",)}

def test_taskbar_windows_honors_preferred_ids():
    provider = build_provider_view(_provider(windows={"session": _window("5H", 90), "weekly": _window("Weekly", 80)}), NOW)
    assert [w.window_id for w in taskbar_windows(provider, preferred_ids=("weekly",))] == ["weekly"]
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_settings_and_models.py tests/test_ui_models.py -q`

Expected: FAIL — ยังไม่มี key/signature ใหม่

- [ ] **Step 3: Implement validation and plumbing**

`settings.py`: เพิ่ม `"taskbar_windows": {}` ใน `DEFAULT_DISPLAY`; whitelist id set

```python
TASKBAR_WINDOW_IDS = frozenset(
    {"session", "weekly", "monthly", "code_review", "pro", "flash", "flash_lite"}
)
```

validate หลัง display merge (filter `key in display` เดิมจะผ่าน key ใหม่เข้ามาเอง):

```python
raw_tb = display.get("taskbar_windows")
clean_tb: dict[str, tuple[str, ...]] = {}
if isinstance(raw_tb, dict):
    for provider_id, ids in raw_tb.items():
        pid = str(provider_id).strip().lower()
        if pid not in styles or not isinstance(ids, (list, tuple)):
            continue
        sequence = tuple(
            wid for wid in (str(value).strip().lower() for value in ids)
            if wid in TASKBAR_WINDOW_IDS
        )
        if sequence:
            clean_tb[pid] = sequence
display["taskbar_windows"] = clean_tb
```

`ui_models.taskbar_windows(provider, preferred_ids=None)`: branch codex/agy/gemini ใช้ `tuple(preferred_ids)` แทน tuple default เมื่อได้รับค่า

`taskbar_widget._render_segments()`: อ่าน `(settings.display.get("taskbar_windows") or {}).get(provider.provider_id)` แล้วส่งเป็น `preferred_ids`

- [ ] **Step 4: Run focused tests then full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_settings_and_models.py tests/test_ui_models.py -q` แล้ว `-m pytest -q`

Expected: PASS (default `{}` ต้องคงพฤติกรรม Task 1 ไว้ครบ)

- [ ] **Step 5: Commit**

```powershell
git add settings.py ui_models.py taskbar_widget.py tests/
git commit -m "feat: configurable per-provider taskbar window selection"
```

---

### Task 5: เอกสารและ acceptance

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

ในหัวข้อ "การใช้งาน" และ "แหล่งข้อมูล → Codex" เพิ่ม:

- Taskbar/dashboard แสดงโควต้า Codex ทั้งหน้าต่าง 5 ชั่วโมง (`5H`) และรายสัปดาห์ (`W`) พร้อมเวลา reset แยกกัน
- Label สรุปมาจาก duration จริงที่ API รายงาน (เช่น `4H`) ไม่ใช่ค่าตายตัว
- ตัวอย่าง `data/config.json`:

```json
{
  "display": {
    "taskbar_windows": { "codex": ["session", "weekly"] }
  }
}
```

- [ ] **Step 2: Final verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\app.py --refresh
.\.venv\Scripts\python.exe .\app.py --diagnostics
```

Expected: tests ผ่านทั้งหมด, `--refresh` รายงาน `[codex] ok: 5H ...% left, Weekly ...% left`, diagnostics ไม่มี token/header หลุด

- [ ] **Step 3: Commit**

```powershell
git add README.md
git commit -m "docs: document Codex 5-hour quota display and taskbar window config"
```

---

## Acceptance criteria

1. Taskbar overlay แสดง `Codex <left>% 5H ·<countdown> · <left>% W ·<countdown>` เมื่อ live API คืนทั้งสอง window
2. เมื่อ backend ส่ง `limit_window_seconds` อื่นที่ไม่ใช่ 18000/604800 (เช่น 14400) window ถูกจับถูกประเภทและ label สะท้อนชั่วโมงจริง (`4H`)
3. Plan ที่มีเฉพาะ weekly หรือเฉพาะ session ยังแสดงผลถูกต้อง (ไม่มีช่องว่าง)
4. `display.taskbar_windows.codex = ["weekly"]` คืนพฤติกรรม weekly-only แบบเดิมได้
5. Notification threshold (20/10/5%) ยิงแยกต่อ window ตาม reset identity เดิม — ไม่ต้องแก้ `notifications.py`
6. Suite เดิมผ่านทั้งหมดโดยไม่ต้อง migrate state (schema v3 ไม่เปลี่ยน)
