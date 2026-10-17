# iOS Shortcut: push today's Apple Health data to the backend

The live-wearable path with no native app (BACKEND_PLAN C1). Once a day an iOS Shortcuts
automation reads today's Health samples and POSTs them to `POST /api/metrics` as one batch.
Re-running is safe: the endpoint is idempotent on `(type, recorded_at, source)`, and a changed
value for the same key (e.g. a higher step count later in the day) is an update, not a new row.

## 1. Get a device token (once)

The Shortcut cannot log in, so it uses a long-lived device token sent as `X-Device-Token`.

```bash
# log in (or use the web app's token)
TOKEN=$(curl -s -X POST https://<API_HOST>/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"demo1234"}' | jq -r .access_token)

# create the device token — the raw value is shown only once
curl -s -X POST https://<API_HOST>/api/auth/device-token \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"label":"Taro iPhone Shortcut"}'
# → {"id":"…","token":"dt_XXXXXXXX…","label":"Taro iPhone Shortcut"}
```

(`python -m scripts.seed` also prints a fresh device token for the demo user.)

## 2. Request the backend expects

```
POST https://<API_HOST>/api/metrics
Content-Type: application/json
X-Device-Token: dt_XXXXXXXX…
```

```json
{
  "source": "shortcut",
  "samples": [
    {"type": "steps",                "value": 8421, "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "distance_m",           "value": 6120, "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "active_energy_kcal",   "value": 412,  "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "resting_hr",           "value": 61,   "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "hrv_sdnn_ms",          "value": 38.5, "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "spo2_pct",             "value": 97,   "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "walking_speed_mps",    "value": 1.18, "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "step_length_m",        "value": 0.66, "recorded_at": "2026-10-17T00:00:00+09:00"},
    {"type": "sleep_asleep_minutes", "value": 402,  "recorded_at": "2026-10-17T00:00:00+09:00"}
  ]
}
```

Rules:
- `recorded_at` = **today at 00:00 in your local offset** for daily values (the same key every
  run, so later runs update instead of duplicating). Sleep uses the **wake-up date**.
- Values must be in the units below; out-of-range values are returned in `rejected` with a reason
  and the rest of the batch is still stored.

| type | unit | from Health |
|------|------|-------------|
| `steps` | count | Steps — **Sum** of today |
| `distance_m` | metres | Walking + Running Distance — Sum (km × 1000) |
| `active_energy_kcal` | kcal | Active Energy — Sum |
| `exercise_minutes` | min | Exercise Minutes — Sum |
| `resting_hr` | bpm | Resting Heart Rate — latest |
| `walking_hr_avg` | bpm | Walking Heart Rate Average — latest |
| `hrv_sdnn_ms` | ms | Heart Rate Variability — Average |
| `spo2_pct` | % (0–100) | Blood Oxygen — Average (send 97, not 0.97) |
| `respiratory_rate` | breaths/min | Respiratory Rate — Average |
| `vo2max` | mL/kg/min | Cardio Fitness — latest |
| `walking_speed_mps` | m/s | Walking Speed — Average (km/h ÷ 3.6) |
| `step_length_m` | m | Walking Step Length — Average (cm ÷ 100) |
| `double_support_pct` | % | Double Support Time — Average |
| `walking_asymmetry_pct` | % | Walking Asymmetry — Average |
| `walking_steadiness` | % | Walking Steadiness — latest |
| `sleep_asleep_minutes` | min | Sleep — total asleep last night |
| `sleep_inbed_minutes` | min | Sleep — In Bed last night |
| `weight_kg` | kg | Weight — latest |

The full registry (with plausible ranges) is `METRIC_TYPES` in `backend/app/schemas/metrics.py`.

## 3. Build the Shortcut (Shortcuts app → + → new shortcut "Health → Advocate")

Allow Health access when asked (Settings → Health → Data Access & Devices → Shortcuts → turn on
the categories you use).

1. **Date for the batch**
   - Add **Date** → *Current Date*.
   - Add **Format Date** → Format: *Custom*, string `yyyy-MM-dd'T'00:00:00ZZZZZ` → rename the
     variable to `Day` (e.g. `2026-10-17T00:00:00+09:00`).
2. **Steps (sum)**
   - **Find Health Samples** where *Type is Steps* and *Start Date is today*.
   - **Calculate Statistics** → *Sum* of *Health Samples* → rename to `Steps`.
   - Repeat for *Walking + Running Distance* (then **Calculate** `× 1000` → `DistanceM`) and
     *Active Energy* (`ActiveKcal`).
3. **Level metrics (latest or average)**
   - **Find Health Samples** where *Type is Resting Heart Rate*, *Start Date is in the last 1 day*,
     *Sort by Start Date*, *Order Latest First*, *Limit 1* → **Get Details of Health Samples** →
     *Value* → rename `RestingHR`.
   - *Heart Rate Variability*, *Blood Oxygen Saturation*, *Walking Speed*, *Walking Step Length*:
     Find (last 1 day) → **Calculate Statistics** → *Average* → rename `HRV`, `SpO2`,
     `WalkSpeed`, `StepLen`. Add **Calculate** `WalkSpeed ÷ 3.6` → `WalkSpeedMps` and
     `StepLen ÷ 100` → `StepLenM` if Shortcuts reports km/h and cm. If SpO2 comes out as
     `0.97`, add **Calculate** `× 100`.
4. **Sleep (last night)**
   - **Find Health Samples** where *Type is Sleep Analysis*, *Start Date is in the last 1 day*,
     *Value is Asleep* (iOS 16+: include *Core*, *Deep*, *REM*).
   - **Repeat with Each** sample → **Get Details of Health Samples** → *Duration* → **Add to
     Variable** `Durations`. After the loop **Calculate Statistics** → *Sum* of `Durations`
     → **Calculate** `÷ 60` if the duration is in seconds → rename `AsleepMin`.
5. **Body** — **Text** action with the JSON from section 2, inserting the variables
   (`Day`, `Steps`, `DistanceM`, `RestingHR`, …) in place of the numbers. Remove lines for
   metrics you do not collect — every line needs a number (an empty value makes the JSON invalid).
6. **Send** — **Get Contents of URL**:
   - URL `https://<API_HOST>/api/metrics`, Method **POST**.
   - Headers: `Content-Type: application/json`, `X-Device-Token: dt_…`.
   - Request Body: **File** → the *Text* from step 5. (Or use *JSON* body with a Dictionary;
     the Text route is easier to keep exact.)
7. **Check** — **Get Dictionary from Input**, then **Show Notification** with
   `inserted` / `updated` / `rejected` (optional; useful while testing).

## 4. Automation (runs every evening)

Shortcuts → **Automation** → **+** → **Time of Day** → 21:30, Daily → **Run Immediately**
(turn off "Notify When Run" if you like) → action **Run Shortcut** "Health → Advocate".
A second run at 08:00 picks up last night's sleep sooner.

## 5. Verify

```bash
curl -s https://<API_HOST>/api/metrics/summary?days=7 -H "X-Device-Token: dt_…" | jq '.metrics | keys'
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `401 Invalid device token` | Token mistyped or deleted. Create a new one (step 1); the header name is exactly `X-Device-Token`. |
| `401 Not authenticated` | Header missing — check *Headers* in **Get Contents of URL** (not in the body). |
| `422 Unprocessable` | Body is not valid JSON: an empty variable produced `"value": ,` or a comma is missing. Add **Show Result** before step 6 to inspect the text. `recorded_at` must be ISO 8601 with offset. |
| `rejected: [{index, reason: "out_of_range:spo2_pct…"}]` | Value in the wrong unit (0.97 instead of 97, km/h instead of m/s, cm instead of m). |
| `rejected: unknown_type:…` | Use the type names in the table exactly (`resting_hr`, not `restingHeartRate`). |
| Duplicated numbers / values not changing | Check `recorded_at`: it must be today's `00:00` with the same offset every run; otherwise each run creates a new key. |
| Steps lower than the Health app | Use **Calculate Statistics → Sum** over *Start Date is today*, not "Limit 1". |
| Automation does not run | iOS needs the phone unlocked once after reboot; ensure *Run Immediately* is on and Health access for Shortcuts is granted. |
| Sleep is 0 | No Apple Watch sleep tracking / Sleep Focus that night, or the *Value is Asleep* filter excludes *Core/Deep/REM* on iOS 16+. |
| Network error | The API host must be HTTPS and reachable from the phone (not `localhost`); on Render the first request after idle can take ~30 s. |
