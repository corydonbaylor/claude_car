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

### `vision_loop.py`
Main control loop integrating everything, running as two concurrent threads.

**Key class:** `VisionControlLoop`
- **Reflex loop** (fast, `--reflex-interval`, default 0.3s): the only thread that touches the camera. Captures a frame, runs the OpenCV obstacle check, and either does a quick evasive turn or keeps driving continuously in the current target direction. No stop-start between ticks — this is what makes driving smooth instead of jerky.
- **Reasoning loop** (slow, `--reasoning-interval`, default 2.0s): reads the most recently captured frame and asks Claude for a direction toward the goal (currently: navigate toward a roll of duct tape). Updates the target direction the reflex loop drives toward.

This splits responsibilities: OpenCV handles fast "reflexes" (imminent collision avoidance, zero API latency, motors never fully stop between ticks), Claude handles slower "reasoning" (steering toward the goal, searching when the target isn't visible, stopping when arrived).

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
python test_motors.py

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
# Simulation mode (dev machine)
python test_motors.py

# Real GPIO (Raspberry Pi)
python test_motors.py --gpio
```

### Run vision control loop
```bash
# Simulation (mock camera, no GPIO)
python vision_loop.py --simulate --iterations 5

# Real hardware, reasoning every 2s, reflex tick every 0.3s (defaults)
python vision_loop.py --iterations 10

# Faster reasoning cadence, slower reflex tick
python vision_loop.py --reasoning-interval 1.0 --reflex-interval 0.5

# Infinite loop (Ctrl+C to stop)
python vision_loop.py
```

### Advanced options
```bash
python vision_loop.py --help
```

Options:
- `--iterations N`: Run N Claude reasoning cycles then stop (default: infinite)
- `--reasoning-interval SECS`: Seconds between Claude calls (default: 2.0)
- `--reflex-interval SECS`: Seconds between reflex/motor ticks (default: 0.3)
- `--simulate`: Use simulation mode (mock camera, no GPIO)
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
The prompt currently targets a specific goal — navigating toward a shoe — and asks Claude for two lines:
- `DIRECTION: <word>` — parsed to drive the car
- `SEEN: <short description>` — not used for driving, just logged via `[Claude sees] ...` so you can check what Claude is actually picking up in the frame

This makes it easy to tell a recognition problem (target not identified despite being visible) apart from a camera problem (target not legible in the captured frame at all) — just tail the logs and compare what Claude reports seeing against what's actually in front of the car.

Tell Claude to search (turn) when the target isn't visible, and to stop when it fills the frame. Can be tuned later by changing the prompt text in `VisionControlLoop.get_next_action()`.

### Why 1280x720 instead of 640x480?
The original lower resolution made it harder for Claude to pick out small/distant objects (e.g. a duct tape roll blended into the floor). Bumped up in `camera.py` for more detail — if this turns out to slow down capture or the API call too much on the Pi, drop it back down.

### Why threads instead of a single loop?
The old design captured a frame, blocked on a full Claude API round-trip, moved briefly, then stopped — every single cycle. That's inherently stop-and-go. Splitting into a fast reflex thread (drives continuously, reacts to obstacles instantly) and a slow reasoning thread (only updates the *target direction* every couple seconds) means the car keeps moving smoothly while Claude "thinks" in the background.

Only the reflex thread touches the camera, to avoid two threads fighting over the hardware. The reasoning thread reads whatever frame the reflex thread most recently captured.

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
1. All 4 wheels spin when `test_motors.py` runs
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
