"""
Linear Stage Scanner
====================
Controls a DC motor via BTS7960 driver, reads position from a Phidget encoder,
and reads measurements from a Mitutoyo micrometer over Ethernet.

Modes (entered at the main menu):
  scan   - Run an automated scan with PID speed control
  fwd    - Manually jog forward (runs until you press Enter)
  rev    - Manually jog reverse (runs until you press Enter)
  zero   - Null (reset) the encoder position
  quit   - Exit

Hardware pin assignments (BCM):
  R_EN  = 17,  L_EN  = 27
  RPWM  = 12,  LPWM  = 13
"""

import csv
import sys
import time
import threading
import struct
import socket
from datetime import datetime

# ──────────────────────────────────────────────
# Dependencies
# ──────────────────────────────────────────────
try:
    from gpiozero import PWMOutputDevice, DigitalOutputDevice
except ImportError:
    raise SystemExit("Install 'gpiozero': sudo apt install python3-gpiozero")

try:
    from Phidget22.Devices.Encoder import Encoder
    from Phidget22.PhidgetException import PhidgetException
except ImportError:
    raise SystemExit("Install 'phidget22': pip install phidget22")


# ══════════════════════════════════════════════
# CONFIGURATION  – change values here
# ══════════════════════════════════════════════

# Motor driver pins (BCM)
PIN_R_EN = 17
PIN_L_EN = 27
PIN_RPWM = 13
PIN_LPWM = 12

# Encoder
ENCODER_HUB_PORT    = 0
ENCODER_IS_HUB_PORT = False
ENCODER_CHANNEL     = 0

# Micrometer Ethernet
MICROMETER_IP   = '10.0.0.3'
MICROMETER_PORT = 24683

# Micrometer binary protocol (from Wireshark)
HANDSHAKE_1 = bytes.fromhex("10000000 02 00 f000 00000000 04000000 37000000".replace(" ", ""))
HANDSHAKE_2 = bytes.fromhex("18000000 02 00 f000 00000000 0c000000 31000000 01000000 10ff0000".replace(" ", ""))
POLL_CMD    = bytes.fromhex("14000000 02 00 f000 00000000 08000000 41000000 00000000".replace(" ", ""))

# Scan parameters
ENCODER_COUNTS_PER_MM = 50      # 1 count = 20 µm  →  50 counts = 1 mm
POLL_RATE_HZ          = 20      # DROPPED TO 20Hz to prevent quantization noise
SCAN_SPEED_MM_S       = 2.0     # target forward speed (mm/s)
MAX_SPEED_MM_S        = 4.0     # hard ceiling for PID output

# Approach-to-zero (return trip): open-loop duty cycle
RETURN_DUTY           = 0.15    # ~15 % – slow enough to not slam the hard stop
ZERO_DECEL_ZONE       = 25      # ~0.5 mm deceleration zone

# Stall / collision detection
STALL_TIME_S          = 0.10    # seconds — forward pass and jog forward
STALL_TIME_RETURN_S   = 0.05    # seconds — reverse jog and scan return
STALL_MIN_COUNTS      = 2       # counts that must change within the window

# PID gains tuned for 20Hz loop rate
KP = 0.0002
KI = 0.001
KD = 0.00


# ══════════════════════════════════════════════
# MOTOR DRIVER
# ══════════════════════════════════════════════

class MotorDriver:
    def __init__(self):
        self.r_en  = DigitalOutputDevice(PIN_R_EN)
        self.l_en  = DigitalOutputDevice(PIN_L_EN)
        self.rpwm  = PWMOutputDevice(PIN_RPWM)
        self.lpwm  = PWMOutputDevice(PIN_LPWM)
        self.r_en.on()
        self.l_en.on()

    def forward(self, duty: float):
        duty = max(0.0, min(1.0, duty))
        self.lpwm.value = 0.0
        self.rpwm.value = duty

    def reverse(self, duty: float):
        duty = max(0.0, min(1.0, duty))
        self.rpwm.value = 0.0
        self.lpwm.value = duty

    def stop(self):
        self.rpwm.value = 0.0
        self.lpwm.value = 0.0

    def shutdown(self):
        self.stop()
        self.r_en.off()
        self.l_en.off()


# ══════════════════════════════════════════════
# ENCODER
# ══════════════════════════════════════════════

class EncoderReader:
    def __init__(self):
        self._offset = 0
        self.enc = Encoder()
        self.enc.setHubPort(ENCODER_HUB_PORT)
        self.enc.setIsHubPortDevice(ENCODER_IS_HUB_PORT)
        self.enc.setChannel(ENCODER_CHANNEL)
        self.enc.openWaitForAttachment(5000)
        min_di = self.enc.getMinDataInterval()
        self.enc.setDataInterval(min_di)

    def position(self) -> int:
        return self.enc.getPosition() - self._offset

    def zero(self):
        self._offset = self.enc.getPosition()
        print(f"Encoder zeroed. Raw hardware position was {self._offset}.")

    def close(self):
        self.enc.close()


# ══════════════════════════════════════════════
# MICROMETER
# ══════════════════════════════════════════════

class Micrometer:
    def __init__(self):
        self.latest_mm: float = 0.0
        self._sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(2.0)
        s.connect((MICROMETER_IP, MICROMETER_PORT))
        s.sendall(HANDSHAKE_1)
        s.recv(1024)
        s.sendall(HANDSHAKE_2)
        s.settimeout(0.5)
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk: break
            except socket.timeout: break

        s.settimeout(1.0)
        self._sock = s
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print("Micrometer connected.")

    def _poll_loop(self):
        while self._running:
            try:
                self._sock.sendall(POLL_CMD)
                data = self._sock.recv(1024)
                if data and len(data) == 72:
                    raw_int = struct.unpack('<i', data[64:68])[0]
                    self.latest_mm = raw_int * 0.0001
            except Exception:
                pass

    def close(self):
        self._running = False
        if self._sock:
            self._sock.close()


# ══════════════════════════════════════════════
# STALL DETECTOR
# ══════════════════════════════════════════════

class StallDetector:
    def __init__(self, encoder: EncoderReader, stall_time: float = STALL_TIME_S):
        self._enc       = encoder
        self._stall_time = stall_time
        self._last_pos  = encoder.position()
        self._last_t    = time.monotonic()
        self.stalled    = False

    def reset(self):
        self._last_pos = self._enc.position()
        self._last_t   = time.monotonic()
        self.stalled   = False

    def update(self) -> bool:
        now = time.monotonic()
        pos = self._enc.position()
        if abs(pos - self._last_pos) >= STALL_MIN_COUNTS:
            self._last_pos = pos
            self._last_t   = now
        elif now - self._last_t > self._stall_time:
            self.stalled = True
        return self.stalled


# ══════════════════════════════════════════════
# PID CONTROLLER
# ══════════════════════════════════════════════

class PID:
    def __init__(self, kp, ki, kd, output_min=0.0, output_max=1.0):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.out_min, self.out_max = output_min, output_max
        self._integral = 0.0
        self._prev_err = 0.0

    def reset(self):
        self._integral  = 0.0
        self._prev_err  = 0.0

    def compute(self, error: float, dt: float) -> float:
        self._integral += error * dt
        derivative      = (error - self._prev_err) / dt if dt > 0 else 0.0
        self._prev_err  = error
        output = (self.kp * error) + (self.ki * self._integral) + (self.kd * derivative)
        return max(self.out_min, min(self.out_max, output))


# ══════════════════════════════════════════════
# SCAN ROUTINE
# ══════════════════════════════════════════════

def run_scan(motor: MotorDriver, enc: EncoderReader, mic: Micrometer):
    print()
    raw = input("Enter scan target as encoder counts or mm (e.g. '500' or '10mm'): ").strip()
    if raw.lower().endswith("mm"):
        target_counts = int(float(raw[:-2]) * ENCODER_COUNTS_PER_MM)
    else:
        target_counts = int(raw)

    speed_input = input(f"Target forward speed mm/s [{SCAN_SPEED_MM_S}]: ").strip()
    target_speed_mm_s = float(speed_input) if speed_input else SCAN_SPEED_MM_S
    target_speed_mm_s = min(target_speed_mm_s, MAX_SPEED_MM_S)
    target_speed_cps = target_speed_mm_s * ENCODER_COUNTS_PER_MM

    ts_str  = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = f"scan_{ts_str}.csv"

    print(f"\nScan to {target_counts} counts ({target_counts/ENCODER_COUNTS_PER_MM:.3f} mm)")
    print(f"Speed target: {target_speed_mm_s} mm/s  |  Output: {outfile}")
    print("Starting in 2 seconds…  (Ctrl+C aborts)\n")
    time.sleep(2)

    pid     = PID(KP, KI, KD, output_min=0.0, output_max=0.08)
    stall   = StallDetector(enc)
    dt      = 1.0 / POLL_RATE_HZ
    rows    = []

    t_start     = time.monotonic()
    prev_pos    = enc.position()
    prev_t      = t_start

    print("[SCAN] Forward pass started.")
    try:
        while True:
            loop_start = time.monotonic()

            pos       = enc.position()
            elapsed   = loop_start - t_start
            mm_reading = mic.latest_mm

            now = time.monotonic()
            actual_cps = (pos - prev_pos) / (now - prev_t) if (now - prev_t) > 0 else 0.0
            prev_pos, prev_t = pos, now

            error = target_speed_cps - actual_cps
            duty  = pid.compute(error, dt)
            motor.forward(duty)

            rows.append((elapsed, pos, mm_reading, actual_cps, duty))

            if stall.update():
                print("\n[STALL DETECTED] Motor stopped during forward pass. Aborting.")
                motor.stop()
                return

            if pos >= target_counts:
                break

            elapsed_loop = time.monotonic() - loop_start
            sleep_t = dt - elapsed_loop
            if sleep_t > 0:
                time.sleep(sleep_t)

    except KeyboardInterrupt:
        print("\n[ABORTED] Scan interrupted by user.")
        motor.stop()
        _save_csv(rows, outfile)
        return

    motor.stop()
    print(f"[SCAN] Forward pass complete at position {enc.position()} counts.")
    time.sleep(0.3)

    print("[SCAN] Returning to zero…")
    stall.reset()
    stall = StallDetector(enc, stall_time=STALL_TIME_RETURN_S)
    try:
        while True:
            pos = enc.position()
            if pos <= ZERO_DECEL_ZONE:
                duty = RETURN_DUTY * 0.5
            else:
                duty = RETURN_DUTY
            motor.reverse(duty)

            if stall.update():
                print("\n[HARD STOP REACHED] Motor stopped during return.")
                break
            time.sleep(dt)
    except KeyboardInterrupt:
        print("\n[ABORTED] Return interrupted by user.")

    motor.stop()
    print(f"[SCAN] Returned. Final position: {enc.position()} counts.")
    _save_csv(rows, outfile)


def _save_csv(rows, filename):
    if not rows:
        return
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_s", "encoder_counts", "micrometer_mm", "actual_cps", "duty_out"])
        writer.writerows(rows)
    print(f"[DATA] {len(rows)} samples saved to '{filename}'.")


# ══════════════════════════════════════════════
# MANUAL JOG
# ══════════════════════════════════════════════

def jog(motor: MotorDriver, enc: EncoderReader, direction: str):
    duty = 0.05  # Reduced from 0.1 so it doesn't fly out of control
    print(f"Jogging {'forward' if direction == 'fwd' else 'reverse'} at {duty*100:.0f}% duty.")
    print("Press Enter to stop.")

    stop_event = threading.Event()

    def _motor_loop():
        stall = StallDetector(enc, stall_time=STALL_TIME_S if direction == 'fwd' else STALL_TIME_RETURN_S)
        while not stop_event.is_set():
            if direction == 'fwd': motor.forward(duty)
            else: motor.reverse(duty)

            if stall.update():
                print("\n[STALL DETECTED] Stopping jog.")
                stop_event.set()
                break
            time.sleep(0.02)
        motor.stop()

    t = threading.Thread(target=_motor_loop, daemon=True)
    t.start()
    input()
    stop_event.set()
    t.join()
    print(f"Jog stopped. Position: {enc.position()} counts ({enc.position()/ENCODER_COUNTS_PER_MM:.3f} mm)")


# ══════════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════════

MENU = """
╔══════════════════════════════╗
║   Linear Stage Controller    ║
╠══════════════════════════════╣
║  scan  – run automated scan  ║
║  fwd   – jog forward         ║
║  rev   – jog reverse         ║
║  zero  – null encoder        ║
║  quit  – exit                ║
╚══════════════════════════════╝
"""

_display_active = threading.Event()
_display_active.set()

def _live_display_loop(enc: EncoderReader, mic: Micrometer):
    while True:
        _display_active.wait()
        pos = enc.position()
        mm  = mic.latest_mm
        sys.stdout.write(
            f"\r  Encoder: {pos:>6} counts "
            f"({pos / ENCODER_COUNTS_PER_MM:>8.3f} mm)   "
            f"Micrometer: {mm:>10.4f} mm    "
        )
        sys.stdout.flush()
        time.sleep(0.1)

def _pause_display():
    _display_active.clear()
    sys.stdout.write("\n")
    sys.stdout.flush()

def _resume_display():
    _display_active.set()

def main():
    print(MENU)
    print("Initialising motor driver…")
    motor = MotorDriver()
    print("Connecting to encoder…")
    try:
        enc = EncoderReader()
        print(f"Encoder attached. Position: {enc.position()} counts.")
    except PhidgetException as e:
        motor.shutdown()
        raise SystemExit(f"Encoder failed: {e}")

    print("Connecting to micrometer…")
    mic = Micrometer()
    try:
        mic.connect()
    except Exception as e:
        motor.shutdown()
        enc.close()
        raise SystemExit(f"Micrometer failed: {e}")

    display_thread = threading.Thread(target=_live_display_loop, args=(enc, mic), daemon=True)
    display_thread.start()

    try:
        while True:
            cmd = input("\n> ").strip().lower()
            if cmd == 'scan':
                _pause_display()
                run_scan(motor, enc, mic)
                _resume_display()
            elif cmd == 'fwd':
                _pause_display()
                jog(motor, enc, 'fwd')
                _resume_display()
            elif cmd == 'rev':
                _pause_display()
                jog(motor, enc, 'rev')
                _resume_display()
            elif cmd == 'zero':
                _pause_display()
                enc.zero()
                _resume_display()
            elif cmd in ('quit', 'exit', 'q'): break
            elif cmd == '': pass
            else: print("Unknown command. Try: scan | fwd | rev | zero | quit")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        print("Shutting down…")
        motor.shutdown()
        enc.close()
        mic.close()
        print("Done.")

if __name__ == "__main__":
    main()