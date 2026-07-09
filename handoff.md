# Handoff: Pan-Tilt Integration Test

## Scope

One job: verify everything on the car still works after the Arducam Pan Tilt Platform (SKU B0283) was wired in, and that the new pan-tilt works. This is a test session, not a feature-building session.

## Hard rule: never actuate the servos and the L298N motors at the same time

The pan-tilt servos are powered from the Pi's 5V rail (Pin 4). The power budget covers each load individually but the margin for simultaneous worst-case draw is thin. Therefore:

- Never send motor commands while a servo move is in progress.
- Never command a servo move while motors are running.
- Every test script must stop and settle one subsystem (motors fully stopped via GPIO low + enable off, or servos idle for >= 500ms after last move) before touching the other.
- If writing any combined routine (e.g. "look around then drive"), enforce this with explicit sequencing: servo move -> wait for completion + 500ms -> motor action -> full stop -> next servo move. No overlap, no exceptions.
- Do not "fix" this constraint or optimize it away, even if tests pass. It is a deliberate power-budget rule, not a bug.

## Hardware state

### Pi power

Pi is powered by USB-C from a 5V/3A brick (MacBook charger, 5V profile confirmed). Motors are powered by a separate 4xAA battery pack through the L298N — motor current never touches the Pi.

### Motor wiring (pre-existing, known working)

| Physical Pin | Function                                        |
| ------------ | ----------------------------------------------- |
| Pin 2        | 5V -> L298N logic terminal                      |
| Pin 6        | GND -> L298N GND (shared with battery negative) |
| Pin 11       | GPIO17 -> IN2 (right backward)                  |
| Pin 12       | GPIO18 -> IN1 (right forward)                   |
| Pin 13       | GPIO27 -> IN4 (left backward)                   |
| Pin 15       | GPIO22 -> IN3 (left forward)                    |
| Pin 18       | GPIO24 -> ENB (left enable)                     |
| Pin 22       | GPIO25 -> ENA (right enable)                    |

L298N 5V-EN jumper is removed (deliberate — do not suggest re-bridging it).

### Pan-tilt wiring (new, NOT yet visually verified — see Gate 0)

Intended mapping:

| Wire                  | Pi Physical Pin         |
| --------------------- | ----------------------- |
| Servo board power (+) | Pin 4 (5V)              |
| Servo board GND       | Pin 9 (GND)             |
| SDA                   | Pin 3 (GPIO2, I2C1 SDA) |
| SCL                   | Pin 5 (GPIO3, I2C1 SCL) |

The board is PCA9685-based (expected I2C address 0x40). The Pi does not drive servo PWM directly; it sends I2C commands and the board generates servo signals.

**Servo calibration (confirmed on physical hardware, 2026-07-09):**
- Pan channel 0: forward = 90°, left = 0°, right = 180°.
- Tilt channel 1: forward = 90° (mount was physically readjusted on 2026-07-09 — an earlier reading of 180° no longer applies).
- Both axes now have forward safely in the middle of the 0-180 range, so normal ± nudges work on both without hitting a limit.
- `tests/test_pan_tilt.py` and `center_pan_tilt.py` are already updated to use these values.

Camera: Arducam IMX219 on the CSI ribbon, unchanged, known working via picamera2.

## Test sequence (in order, do not skip gates)

### Gate 0 — wiring verification before anything else

The physical pin mapping above was intended but never visually confirmed against the header. Before running any code that powers servos:

1. Ask the user to confirm they have double-checked the four pan-tilt wires against the table above (or had the photo verified).
2. Run `sudo i2cdetect -y 1`. Expected: device at `40`. If the grid is empty, STOP — wiring is wrong, do not proceed, report back.

### Test 1 — baseline: nothing regressed

1. Confirm the Pi boots clean and `vcgencmd get_throttled` returns `throttled=0x0`.
2. Camera: capture a single frame via the existing picamera2 path. Confirm non-black image.
3. Motors (servos untouched): run the existing `forward.py` briefly (~1s). Confirm motion, confirm clean stop, all GPIO released.

### Test 2 — servos alone (motors fully stopped)

1. Small moves first: command pan to center, then +-20 degrees, then center. Then tilt the same. Watch for jitter or stalling.
2. After each move, check `vcgencmd get_throttled`. Any nonzero value = undervoltage event = STOP and report. This is the primary failure signal for the power budget.
3. Then full-range sweep, slowly. The camera ribbon must stay slack through the whole range — user should watch it during this test.

### Test 3 — alternated operation (the realistic usage pattern)

Sequence, with full stops between phases: pan left -> settle -> drive forward 1s -> full stop -> pan right -> settle -> drive backward 1s -> full stop -> center servos.

- Check `vcgencmd get_throttled` after the full sequence.
- Confirm no camera glitches, no I2C errors, no motor misbehavior at any point.

### Test 4 — camera + servo integration

Capture a frame at pan left, center, and right. Confirm three distinct viewpoints. This proves the pan-tilt does its actual job for the vision loop.

## Failure signals to watch for throughout

- `vcgencmd get_throttled` nonzero -> undervoltage; stop, report which test triggered it.
- I2C errors / device 0x40 disappearing mid-session -> loose wiring; stop.
- Servo jitter or hum at idle -> report but continue cautiously.
- Any lightning-bolt icon if a display is attached -> same as throttled flag.

## Environment notes

- Existing motor code and repo layout: see README_CODE.md in the repo.
- Servo control: use a PCA9685 library (e.g. adafruit-circuitpython-servokit or adafruit-circuitpython-pca9685). Install whatever is missing; the pan-tilt has never been driven from this Pi before, so no library is assumed present.
- GH-S37D servos: operating range 3.6-4.8V, they are being run at 5V from the Pi rail per Arducam's own quick-start wiring. Slightly above nameplate spec by design of the kit — do not flag this as a problem, but keep movements slow and smooth; no rapid full-range slams.

## Out of scope

Vision-loop changes, obstacle avoidance, any refactoring of working motor code, any performance optimization. Test, report, done.
