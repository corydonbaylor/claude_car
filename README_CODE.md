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

### `vision_loop.py`
Main control loop integrating everything.

**Key class:** `VisionControlLoop`
1. Captures image with camera
2. Sends to Claude via vision API
3. Claude responds with direction: forward/backward/left/right/stop
4. Executes the movement
5. Repeats

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
python vision_loop.py --iterations 10 --duration 0.5
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

# Real hardware with 10 iterations, 0.75s per action
python vision_loop.py --iterations 10 --duration 0.75

# Infinite loop (Ctrl+C to stop)
python vision_loop.py
```

### Advanced options
```bash
python vision_loop.py --help
```

Options:
- `--iterations N`: Run N cycles then stop (default: infinite)
- `--duration SECS`: How long each movement lasts (default: 0.5)
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
The prompt is intentionally simple and strict:
- Forces Claude to respond with just one direction word
- Avoids explanations that need parsing
- Can be tuned later by changing the system prompt in `vision_loop.py`

### Movement timing
Each action runs for `--duration` seconds (default 0.5s), then stops. On carpet, 0.5s is usually enough to see motion. Adjust higher if the car barely moves.

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
