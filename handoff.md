# Handoff: Claude-Controlled RC Car — Full Project State

## What this project is

A Raspberry Pi 4 RC car with a camera, driven by a Claude vision loop, with a pan-tilt camera mount added on top. The car searches for its target (currently: a shoe) using only the pan-tilt while sitting still, then aligns its body and drives to approach once found. OpenCV handles fast local obstacle avoidance ("reflexes") during the approach; Claude handles goal-directed reasoning (is the shoe visible? where?) throughout.

Repo: git repo at this directory. SSH into the car: `ssh pi@192.168.1.95`.

---

## Repo structure

| File | Purpose |
|---|---|
| `motor_control.py` | `MotorController` class — GPIO control for the 4 drive motors via L298N. Simulation mode auto-fallback if `RPi.GPIO` isn't installed. |
| `camera.py` | `Camera` class — captures via `rpicam-still`/`raspistill`, `rotation=180` (camera is physically mounted upside-down), clears `captures/` on each run. |
| `reflexes.py` | `ReflexEngine` — Canny edge density check on the near-field of each frame; triggers an evasive turn if an obstacle is detected directly ahead. No API call, runs every tick during the approach phase. |
| `pan_tilt.py` | `PanTilt` — wraps the PCA9685 servo board (`set_pan`, `set_tilt`, `center`). Simulation-mode auto-fallback if the board isn't reachable, same pattern as `MotorController`. `PAN_FORWARD`/`TILT_FORWARD` = 90° each, the hardware-calibrated forward position. |
| `vision_loop.py` | `VisionControlLoop` — single-threaded search -> align -> approach state machine. **SEARCHING**: car fully stopped, pan-tilt sweeps 5 fixed angles (30/60/90/120/150°) asking Claude "is the shoe here?" at each; if the whole sweep misses, pan-tilt re-centers, the car body pivots to a new heading, sweep repeats. **ALIGNING**: pan-tilt re-centers to forward first, then the car body turns to face the direction the shoe was found in (turn duration scaled from the pan offset). **APPROACHING**: drives forward continuously with OpenCV reflexes active, periodically stopping for a clean Claude recheck; if the shoe is lost, returns to SEARCHING. Claude only ever reports structured observations (`FOUND`, `POSITION`) — it never picks a direction, motor/servo actions are derived deterministically in code. Every servo/motor handoff is separated by a settle pause (`servo_motor_settle_time`, minimum 0.5s) per the hard rule below. |
| `center_pan_tilt.py` | Calibration utility — holds the pan-tilt servos at a given angle (default 90/90) so the mount/horn can be physically adjusted. PCA9685 keeps outputting the signal after the script exits. |
| `tests/test_motors.py` | Drives through forward/backward/left/right/stop. Defaults to real GPIO; pass `--simulate` to force simulation. |
| `tests/test_pan_tilt.py` | Small, cautious pan-tilt servo test (±15° nudges only), checks `vcgencmd get_throttled` after every move, aborts on any undervoltage signal. |
| `requirements.txt` | `anthropic`, `opencv-python-headless`, `numpy`, `RPi.GPIO`, `adafruit-circuitpython-servokit` |
| `README_CODE.md` | Usage/setup instructions for the motor+camera+vision-loop side of the project. |

---

## Confirmed hardware state

### Pi

Raspberry Pi 4 (4GB), powered by its own dedicated USB-C 5V/3A supply — untouched by anything else in this build.

### Motors / L298N (original build, tested, working)

| Physical Pin | Function |
|---|---|
| Pin 2 | 5V → L298N logic terminal ("+5V") |
| Pin 6 | GND → L298N GND (shared with battery negative) |
| Pin 11 | GPIO17 → IN2 (right backward) |
| Pin 12 | GPIO18 → IN1 (right forward) |
| Pin 13 | GPIO27 → IN4 (left backward) |
| Pin 15 | GPIO22 → IN3 (left forward) |
| Pin 18 | GPIO24 → ENB (left enable) |
| Pin 22 | GPIO25 → ENA (right enable) |

- Battery: 4x AA Duracell alkaline, ~6.0-6.4V fresh, wired into the L298N's 12V terminal for motor power.
- L298N 5V-EN jumper is **removed/open** — deliberate, because the battery voltage doesn't give the onboard regulator enough headroom to work reliably. Logic power comes from the Pi instead. **Do not re-bridge this jumper without also disconnecting the Pi 5V wire** — having both at once means two regulators fighting for the same node (evaluated as a hypothetical in a separate conversation; not the actual state of this build, just don't combine them).

### Pan-tilt (Arducam Pan Tilt Platform, SKU B0283)

| Wire | Pi Physical Pin |
|---|---|
| Servo board power (+) | Pin 4 (5V — same internal rail as Pin 2, see power budget note below) |
| Servo board GND | Pin 9 (GND) |
| SDA | Pin 3 (GPIO2, I2C1 SDA) |
| SCL | Pin 5 (GPIO3, I2C1 SCL) |

- Board is PCA9685-based, I2C address `0x40`. Pi sends I2C commands; the board generates the actual servo PWM signals — Pi does not drive servo signal pins directly.
- Servos: GH-S37D digital servos, rated 3.6V-4.8V, <350mA each (~700mA combined worst case). Running slightly above nameplate spec off the Pi's 5V rail — accepted tradeoff, not flagged as a bug.
- **Servo channel assignment**: channel 1 = pan (left/right), channel 0 = tilt (up/down). Earlier physical testing (2026-07-09) had these backwards — channel 0 was labeled "pan" but actually swivels the camera up/down. Corrected 2026-07-10 after confirming channel 0 tilts rather than pans on the real mount. If the mount is ever unwired/rewired, re-verify which channel does which before trusting this.
- **Calibration (confirmed on hardware, 2026-07-09)**: pan forward = 90° (0°=left, 180°=right). Tilt forward = 90° (mount was physically adjusted to get here — an earlier reading of 180° no longer applies). Both axes have forward safely in the middle of the 0-180 range.

### Hard rule: never actuate servos and motors at the same time

The pan-tilt servos and the L298N's logic terminal both ultimately draw from the Pi's single internal 5V rail (Pin 2 and Pin 4 are the same rail, not independent supplies). Combined worst-case draw is thin against the Pi's safe GPIO 5V budget. Mitigation, not a bug:

- Never send motor commands while a servo move is in progress, and vice versa.
- Every routine must fully settle one subsystem (motors stopped, or servos idle ≥500ms after last move) before touching the other.
- Any combined routine (e.g. "look around then drive") must sequence explicitly: servo move → settle 500ms → motor action → full stop → next servo move. No overlap.
- Do not "optimize" this away even if tests pass without it.

---

## Confirmed working / tested

- Motors: all 4 directions confirmed via `tests/test_motors.py` on real hardware.
- Camera: capture + rotation fix confirmed (image was upside-down, fixed with `rotation=180`).
- Pan-tilt I2C link: `sudo i2cdetect -y 1` shows device at `0x40`.
- Pan-tilt small movements: `tests/test_pan_tilt.py` ran clean — pan and tilt both nudge ±15° and return, zero `vcgencmd get_throttled` events throughout.
- Pan-tilt calibration: physically adjusted and confirmed, both axes forward = 90°.
- Vision loop pipeline: exercised in simulation mode locally (mock camera, dummy key, simulated servos/motors) — search sweep → align → approach state transitions all run without crashing, including the full-sweep-miss → rotate → re-sweep path and the approach → target-lost → back-to-search path. Not yet run on real hardware or stress-tested for extended real-world runs.

## Not yet done — natural next steps

1. **Pan-tilt full range-of-motion sweep** (only small ±15° nudges have been tested so far). The new search sweep in `vision_loop.py` uses 30-150°, which exceeds the tested ±15° range — do this test before trusting the sweep on hardware.
2. **Alternated servo/motor test** — pan → settle → drive → stop → pan the other way → settle → drive → stop, checking `vcgencmd get_throttled` throughout. Validates the hard-rule sequencing under realistic combined usage. The new search/align state machine does exactly this pattern in production, so this test doubles as validation for it.
3. **Camera + servo integration test** — capture frames at pan left/center/right, confirm three distinct viewpoints.
4. **Wired the pan-tilt into `vision_loop.py`** (done) — search/align/approach state machine described above. Not yet run on real hardware; only exercised in `--simulate` mode so far. `body_turn_seconds_per_degree` (how long the car pivots per degree of pan offset when aligning) is an unverified guess and needs empirical tuning on the car.
5. Personality add-ons from the original hardware handoff (OLED face display, mic/speaker) — not purchased/wired.

---

## Key lessons & gotchas (read before touching wiring or GPIO assignments)

- **Physical pin number ≠ GPIO/BCM number.** Always specify which one a diagram means.
- **The Pi has exactly one internal 5V rail.** Pin 2 and Pin 4 are the same node, not independent budgets — anything drawing from either is drawing from the same source. Keep combined GPIO 5V draw modest (rough community guidance: ~200mA comfortable, ~700mA hard ceiling on some Pi models' combined USB+GPIO fuse).
- **Never tie two active voltage regulators to the same output node** (e.g. L298N onboard regulator + external 5V feed at once) — they'll fight for control of the node continuously, not just during load transients.
- **Camera is mounted upside-down** — `rotation=180` in `camera.py` compensates. If a captured image ever looks inverted again, check this hasn't regressed before assuming a wiring problem.
- **Servo calibration is mount-specific and was found by physical trial** (attach horn, test, detach, readjust) — not something to assume from a datasheet. If servos are ever unmounted/remounted, recheck center position with `center_pan_tilt.py` before trusting angle math elsewhere.
- **`tests/test_motors.py` defaults to real GPIO** (not simulation) — pass `--simulate` to force simulation on a dev machine.
- Alkaline battery sag caused a real undervoltage debugging incident earlier in this build — traced to a wiring short (loose/stray wire at a screw terminal), not a genuine overload. Resolved; mentioned here so a repeat undervoltage symptom isn't assumed to be the same root cause without checking.

## Environment / setup

- SSH: `ssh pi@192.168.1.95`
- Python env on the Pi: `venv` at `~/claude_car/venv` (externally-managed-environment on this OS, so a venv is required, not optional).
- `ANTHROPIC_API_KEY` is exported via `~/.bashrc` on the Pi (persists across sessions).
- Dependencies: `pip install -r requirements.txt` after activating the venv.
