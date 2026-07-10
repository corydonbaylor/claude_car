# RC Car Vision Control

Python code for Claude-controlled RC car using vision API and motor control.

## Architecture

### `motor_control.py`
Low-level motor control via GPIO pins and L298N driver.

**Key class:** `MotorController`
- Supports actual GPIO on Raspberry Pi and simulation mode for dev machines
- Methods: `forward()`, `backward()`, `left()`, `right()`, `stop()`, `move(direction, duration)`
- Handles all 4 motors (front-left, front-right, back-left, back-right)

**GPIO pin mapping** (from L298N wiring):
- Right motors: GPIO 18 (IN1/forward), GPIO 17 (IN2/backward), GPIO 25 (ENA/enable)
- Left motors: GPIO 22 (IN3/forward), GPIO 27 (IN4/backward), GPIO 24 (ENB/enable)

### `camera.py`
Camera capture using Arducam V2 on Raspberry Pi.

**Key class:** `Camera`
- Uses `rpicam-still` (or fallback `raspistill`) on Raspberry Pi
- Provides `mock_capture()` for testing on dev machines
- Returns images as base64-encoded JPEG for Claude API

### `reflexes.py`
Fast, local obstacle detection using OpenCV — no API call, runs every frame.

**Key class:** `ReflexEngine`
- Runs Canny edge detection on the near-field (bottom half) of the frame, split into left/center/right thirds
- A close, flat obstacle (wall, box) shows up as unusually low edge density right in front of the car
- If the center region is blocked, immediately returns an escape direction (toward whichever side has more edge detail / is more open)
- Coarse heuristic, not true depth sensing — thresholds may need tuning per camera/environment

### `pan_tilt.py`
Pan-tilt camera mount control via the Arducam PCA9685 servo board (I2C `0x40`).

**Key class:** `PanTilt`
- Supports the real servo board on the Pi and simulation mode for dev machines, same `use_gpio` pattern as `MotorController`
- Methods: `set_pan(angle)`, `set_tilt(angle)`, `center()`
- `PAN_FORWARD` / `TILT_FORWARD` (both 90°) are the hardware-calibrated forward positions — see handoff.md before changing them

### `vision_loop.py`
Main control loop: a search -> align -> approach state machine, single-threaded.

**Key class:** `VisionControlLoop`
- **SEARCHING**: the car stays fully stopped. The pan-tilt sweeps across a fixed set of angles (default 30/60/90/120/150°, set via `pan_sweep_angles` in code), capturing a frame and asking Claude "is the shoe here?" at each one. If the full sweep finds nothing, the pan-tilt is re-centered and settled, the car body pivots to a new heading, and the sweep repeats.
- **ALIGNING**: once the shoe is found at some pan angle, the camera is re-centered to forward first (and settled), then the car body turns to face the direction the shoe was found in.
- **APPROACHING**: with the shoe roughly dead ahead, the car drives forward continuously — OpenCV reflexes (`--reflex-interval`, default 0.3s) watch every tick for close obstacles — while periodically (`--reasoning-interval`, default 2.0s) stopping just long enough for a clean Claude recheck that the shoe is still visible and centered. If it's lost, control returns to SEARCHING.

Motors and the pan-tilt servos are never actuated at the same time (`--servo-motor-settle`, default 0.5s minimum) — see the hard rule in handoff.md.

**Entry point:** `main()` with CLI arguments

## Setup

### On development machine (macOS/Linux/Windows)

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Test motor control in simulation mode
python tests/test_motors.py --simulate

# Run vision loop in simulation (uses mock camera, no GPIO)
python vision_loop.py --simulate --iterations 3
```

### On Raspberry Pi

```bash
# SSH into Pi
ssh pi@192.168.1.95

# Clone/copy this repo
git clone <repo> claude_car
cd claude_car

# Install dependencies (Pi already has RPi.GPIO)
pip install -r requirements.txt

# Set API key
export ANTHROPIC_API_KEY="your_key_here"

# Run vision loop (real GPIO, real camera)
python vision_loop.py --iterations 10
```

## Usage

### Test motors only
```bash
# Real GPIO (Raspberry Pi) — this is the default
python tests/test_motors.py

# Simulation mode (dev machine, or to force simulation on a Pi)
python tests/test_motors.py --simulate
```

### Run vision control loop
```bash
# Simulation (mock camera, no GPIO/servos)
python vision_loop.py --simulate --iterations 20

# Real hardware, defaults (2s reasoning recheck, 0.3s reflex tick during approach)
python vision_loop.py --iterations 30

# Faster reasoning cadence during approach, slower reflex tick
python vision_loop.py --reasoning-interval 1.0 --reflex-interval 0.5

# Infinite loop (Ctrl+C to stop)
python vision_loop.py
```

### Advanced options
```bash
python vision_loop.py --help
```

Options:
- `--iterations N`: Run N Claude/action ticks in total then stop (default: infinite). Each pan-tilt check during a search sweep and each recheck during approach counts as one tick.
- `--reasoning-interval SECS`: Seconds between Claude rechecks while approaching (default: 2.0)
- `--reflex-interval SECS`: Seconds between reflex/motor ticks while approaching (default: 0.3)
- `--capture-settle SECS`: Seconds to hold the car still before a reasoning recheck captures a frame (default: 0.4)
- `--pan-settle SECS`: Seconds to wait after a pan move before capturing, so the servo has physically arrived (default: 0.3)
- `--servo-motor-settle SECS`: Minimum pause at every servo/motor handoff — don't go below 0.5 (default: 0.5)
- `--simulate`: Use simulation mode (mock camera, no GPIO/servos)
- `--api-key KEY`: Pass API key directly (or use ANTHROPIC_API_KEY env var)

## Design notes

### Why GPIO simulation?
The motor control class detects if `RPi.GPIO` is available. On dev machines without it, the code still runs — it just logs GPIO state instead of actually toggling pins. This lets you:
- Test logic on your laptop
- Verify the command sequence is correct
- Develop without accessing the Pi

### Why mock camera?
Same idea: on non-Pi machines, `camera.py` creates tiny valid JPEG files instead of capturing. This tests the full pipeline (capture → base64 → Claude API → parse) without hardware.

### Vision API prompt
The prompt targets a specific goal — finding a shoe — and asks Claude to report structured observations, not a movement decision:
- `FOUND: <yes|no>`
- `POSITION: <left|center|right|none>` — where the shoe is in the frame
- `SEEN: <short description>` — not used for driving, just logged via `[Claude sees] ...` so you can check what Claude is actually picking up in the frame

Claude never picks a direction itself; `VisionControlLoop` derives every motor/servo action from `found`/`position` deterministically (see `_search_sweep`, `_align_to_target`, `_approach_loop`). This prevents Claude from guessing a direction on a hunch when the target isn't actually visible, and makes it easy to tell a recognition problem (target not identified despite being visible) apart from a camera problem (target not legible in the frame at all) — just tail the logs and compare what Claude reports seeing against what's actually in front of the car. Can be tuned by changing the prompt text in `VisionControlLoop._observe()`.

### Why 1280x720 instead of 640x480?
The original lower resolution made it harder for Claude to pick out small/distant objects (e.g. a duct tape roll blended into the floor). Bumped up in `camera.py` for more detail — if this turns out to slow down capture or the API call too much on the Pi, drop it back down.

### Why a state machine instead of always driving?
The car now searches with the pan-tilt before it ever drives, so SEARCHING and ALIGNING are inherently stop-and-go by design — the hard rule (never move servos and motors at once) makes that unavoidable during a search. APPROACHING is the one phase where continuous motion still matters (once the shoe is found and the car is driving toward it), so it keeps the old fast-reflex / periodic-reasoning-recheck split, just run sequentially in one thread instead of two: a handful of reflex ticks (driving, checking for obstacles) between each clean Claude recheck, rather than two threads racing over shared camera/motor state.

## Troubleshooting

### "RPi.GPIO not available"
You're on a dev machine. Use `--simulate` flag or mock_capture().

### "Camera tools not found"
You're on a dev machine. Use `camera.mock_capture()` or `--simulate` flag.

### API errors
Check:
1. ANTHROPIC_API_KEY is set and valid
2. Network connectivity
3. Claude API is up (https://status.anthropic.com)

### Car doesn't move
On Raspberry Pi, check:
1. All 4 wheels spin when `tests/test_motors.py` runs
2. Battery is connected and charged
3. Motor pins are wired to the GPIO numbers in `motor_control.py`
4. `GPIO.cleanup()` from a prior run isn't still stuck — reboot if stuck

### Car moves wrong direction
Swap the polarity of one motor's wires (physically) — it's a hardware issue, not fixable in code.

## Next steps

Personality add-ons (from handoff doc):
- **Face display:** I2C OLED (GPIO 2/SDA, GPIO 3/SCL)
- **Audio:** USB sound card with mic + speaker

Both can be added without conflicting with current wiring.
