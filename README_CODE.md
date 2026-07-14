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
- Right motors: GPIO 23 (IN1/forward — remapped from GPIO 18 on 2026-07-10, see handoff.md), GPIO 17 (IN2/backward), GPIO 25 (ENA/enable)
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
- Not currently wired into `vision_loop.py` — the search/align/approach state machine doesn't call it. Kept in the repo in case obstacle avoidance gets reintroduced later.

### `pan_tilt.py`
Pan-tilt camera mount control via the Arducam PCA9685 servo board (I2C `0x40`).

**Key class:** `PanTilt`
- Supports the real servo board on the Pi and simulation mode for dev machines, same `use_gpio` pattern as `MotorController`
- Methods: `set_pan(angle)`, `set_tilt(angle)`, `center()`
- `PAN_FORWARD` / `TILT_FORWARD` (both 90°) are the hardware-calibrated forward positions — see handoff.md before changing them

### `vision_loop.py`
Main control loop: a search -> align -> approach state machine, single-threaded. Exactly three modes:

**Key class:** `VisionControlLoop`
- **SEARCHING**: the L298N is fully disabled for the whole mode — all six pins (IN1-IN4 *and* ENA/ENB) held LOW via `MotorController.disable()`, so the driver outputs nothing while the servos work. The pan-tilt sweeps a fixed set of angles (default 30/60/90/120/150°, set via `pan_sweep_angles` in code), capturing a frame and asking Claude "is the shoe here?" at each one. If none of the five show the shoe, the sweep just repeats from the first angle. As soon as one does, it exits immediately for ALIGNING. The next drive command re-enables the driver automatically.
- **ALIGNING**: the camera is re-centered to forward first, then the car body turns toward the direction the shoe was found in. After turning, it takes a fresh photo — if the shoe is still in frame, move on to APPROACHING; if not, back to SEARCHING.
- **APPROACHING**: drive straight forward (`--approach-tick`, default 0.3s per tick) until interrupted (Ctrl+C or `--iterations` budget). No steering or obstacle checks in this mode — see `reflexes.py` above.

Motors and the pan-tilt servos are never actuated at the same time (`--servo-motor-settle`, default 0.5s minimum) — see the hard rule in handoff.md. Every pan-tilt move goes through `_move_pan`/`_center_camera`, the only two places in the code allowed to call the servo API; both force the motors stopped and settled immediately beforehand, unconditionally, so this can't be bypassed by a call site skipping the sequence.

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

# Real hardware, defaults
python vision_loop.py --iterations 30

# Slower forward-drive tick while approaching
python vision_loop.py --approach-tick 0.5

# Infinite loop (Ctrl+C to stop)
python vision_loop.py
```

### Advanced options
```bash
python vision_loop.py --help
```

Options:
- `--iterations N`: Run N Claude/action ticks in total then stop (default: infinite). Each pan-tilt check during a search sweep and each forward-drive tick during approach counts as one tick.
- `--pan-settle SECS`: Seconds to wait after a pan move before capturing, so the servo has physically arrived (default: 0.3)
- `--servo-motor-settle SECS`: Minimum pause at every servo/motor handoff — don't go below 0.5 (default: 0.5)
- `--capture-settle SECS`: Seconds to pause after the align turn before the confirmation photo, so it isn't motion-blurred (default: 0.4)
- `--approach-tick SECS`: Seconds between forward-drive ticks while approaching (default: 0.3)
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

Claude never picks a direction itself; `VisionControlLoop` derives every motor/servo action from `found`/`position` deterministically (see `_search_sweep`, `_align_to_target`, `_approach`). This prevents Claude from guessing a direction on a hunch when the target isn't actually visible, and makes it easy to tell a recognition problem (target not identified despite being visible) apart from a camera problem (target not legible in the frame at all) — just tail the logs and compare what Claude reports seeing against what's actually in front of the car. Can be tuned by changing the prompt text in `VisionControlLoop._observe()`.

### Why 1280x720 instead of 640x480?
The original lower resolution made it harder for Claude to pick out small/distant objects (e.g. a duct tape roll blended into the floor). Bumped up in `camera.py` for more detail — if this turns out to slow down capture or the API call too much on the Pi, drop it back down.

### Why does SEARCHING never touch the drive motors?
Earlier versions of this loop pivoted the car body between search sweeps (to search a new heading) and kept a fast obstacle-avoidance loop running during approach. Both got cut: the pan-tilt hard rule (never move servos and motors at once) meant a lot of stop/settle bookkeeping just to occasionally nudge the car during search, and it made "is the car currently allowed to be moving?" hard to answer at a glance. Now it's an invariant instead of a sequencing exercise: SEARCHING simply never calls anything but `motor.stop()`, full stop.

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
