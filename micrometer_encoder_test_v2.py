"""
200 Hz Data Logger — Micrometer (Ethernet) + Phidget Encoder
=============================================================
Controls (type into the terminal and press Enter):
    s  — Start recording (creates a new timestamped CSV)
    x  — Stop  recording
    q  — Quit

CSV columns:
    timestamp, micrometer_mm, encoder_counts

Requirements:
    pip install phidget22
"""

import csv
import socket
import struct
import threading
import time
import sys
from datetime import datetime
from pathlib import Path

try:
    from Phidget22.Devices.Encoder import Encoder
    from Phidget22.PhidgetException import PhidgetException
except ImportError:
    raise SystemExit("Install 'phidget22': pip install phidget22")


# ──────────────────────────────────────────────
# Configuration — edit these as needed
# ──────────────────────────────────────────────

# Micrometer (ethernet)
MICRO_IP   = "10.0.0.3"
MICRO_PORT = 24683

HANDSHAKE_1 = bytes.fromhex("10 00 00 00 02 00 f0 00 00 00 00 00 04 00 00 00 37 00 00 00".replace(" ", ""))
HANDSHAKE_2 = bytes.fromhex("18 00 00 00 02 00 f0 00 00 00 00 00 0c 00 00 00 31 00 00 00 01 00 00 00 10 ff 00 00".replace(" ", ""))
POLL_CMD    = bytes.fromhex("14 00 00 00 02 00 f0 00 00 00 00 00 08 00 00 00 41 00 00 00 00 00 00 00".replace(" ", ""))

# Phidget encoder — match your hub wiring from app.py
ENCODER_HUB_PORT       = 0
ENCODER_IS_HUB_PORT    = False   # ENC1001 is a VINT device
ENCODER_CHANNEL        = 0
ENCODER_RESOLUTION_UM  = 10      # micrometers per pulse (from app.py)

# Logger
SAMPLE_RATE_HZ  = 200
SAMPLE_PERIOD_S = 1.0 / SAMPLE_RATE_HZ
OUTPUT_DIR      = Path("logs")


# ──────────────────────────────────────────────
# Shared state  (written by device threads, read by logger)
# ──────────────────────────────────────────────
class State:
    lock              = threading.Lock()
    micrometer_mm     = None   # float | None
    encoder_counts    = None   # int   | None
    recording         = False
    csv_path          = None
    csv_writer        = None
    csv_file          = None
    running           = True

state = State()


# ──────────────────────────────────────────────
# Micrometer thread
# ──────────────────────────────────────────────
def micrometer_thread():
    print(f"[Micrometer] Connecting to {MICRO_IP}:{MICRO_PORT} …")
    while state.running:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(3.0)
                s.connect((MICRO_IP, MICRO_PORT))

                # Handshake
                s.sendall(HANDSHAKE_1)
                s.recv(1024)
                s.sendall(HANDSHAKE_2)

                # Drain metadata buffer
                s.settimeout(0.3)
                while True:
                    try:
                        if not s.recv(4096):
                            break
                    except socket.timeout:
                        break

                print("[Micrometer] Connected and ready.")
                s.settimeout(0.5)
                sendall = s.sendall
                recv    = s.recv

                while state.running:
                    sendall(POLL_CMD)
                    data = recv(1024)
                    if data and len(data) == 72:
                        raw_int = struct.unpack('<i', data[64:68])[0]
                        mm = raw_int * 0.0001
                        with state.lock:
                            state.micrometer_mm = mm

        except Exception as e:
            print(f"[Micrometer] Error: {e}. Retrying in 2 s …")
            time.sleep(2)


# ──────────────────────────────────────────────
# Phidget encoder  (open once; poll getPosition() in logger loop)
# ──────────────────────────────────────────────
encoder_device = None

def init_encoder():
    global encoder_device
    try:
        enc = Encoder()
        enc.setHubPort(ENCODER_HUB_PORT)
        enc.setIsHubPortDevice(ENCODER_IS_HUB_PORT)
        enc.setChannel(ENCODER_CHANNEL)
        enc.openWaitForAttachment(5000)

        # Set the fastest possible data interval
        min_di = enc.getMinDataInterval()
        enc.setDataInterval(min_di)

        encoder_device = enc
        print(f"[Encoder] Attached. Min data interval = {min_di} ms.")
        return True
    except PhidgetException as e:
        print(f"[Encoder] Failed to attach: {e}")
        return False


# ──────────────────────────────────────────────
# Recording helpers
# ──────────────────────────────────────────────
def start_recording():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    path  = OUTPUT_DIR / f"data_{ts}.csv"
    f     = open(path, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(["timestamp", "micrometer_mm", "encoder_counts"])
    with state.lock:
        state.csv_path   = path
        state.csv_file   = f
        state.csv_writer = writer
        state.recording  = True
    print(f"\n[Logger] Recording started → {path}")


def stop_recording():
    with state.lock:
        state.recording = False
        if state.csv_file:
            state.csv_file.close()
            state.csv_file   = None
            state.csv_writer = None
    print(f"\n[Logger] Recording stopped. File: {state.csv_path}")


# ──────────────────────────────────────────────
# 200 Hz logger loop  (runs in its own thread)
# ──────────────────────────────────────────────
def logger_loop():
    """
    Runs at 200 Hz. Each tick:
      1. Reads micrometer_mm from shared state.
      2. Calls encoder.getPosition() directly — no event needed.
      3. If recording, writes a CSV row.
      4. If NOT recording, displays values on the terminal via standard out.
    """
    next_tick = time.perf_counter()
    tick_count = 0
    was_recording = False

    while state.running:
        now       = time.perf_counter()
        ts_iso    = datetime.now().isoformat(timespec="microseconds")

        # Read micrometer (written by micrometer_thread)
        with state.lock:
            micro_mm = state.micrometer_mm
            is_recording = state.recording

        # Read encoder by direct poll
        enc_counts = None
        if encoder_device is not None:
            try:
                enc_counts = encoder_device.getPosition()
            except PhidgetException:
                pass

        if is_recording:
            if not was_recording:
                # Add a newline so the live view text doesn't overlap the recording text
                sys.stdout.write("\n") 
                sys.stdout.flush()
                was_recording = True

            # Write row if recording
            with state.lock:
                if state.csv_writer is not None:
                    state.csv_writer.writerow([
                        ts_iso,
                        f"{micro_mm:.4f}" if micro_mm is not None else "",
                        enc_counts if enc_counts is not None else "",
                    ])
        else:
            was_recording = False
            # Display live values at 10 Hz (every 20 ticks of the 200Hz loop)
            tick_count += 1
            if tick_count % 20 == 0:
                mm_str = f"{micro_mm:.4f} mm" if micro_mm is not None else "---"
                enc_str = f"{enc_counts}" if enc_counts is not None else "---"
                # Use \r to overwrite the line, avoiding console spam
                sys.stdout.write(f"\r[Idle] Micro: {mm_str:>10} | Enc: {enc_str:>8} counts   (Type s/x/q + Enter) ")
                sys.stdout.flush()

        # Drift-corrected sleep
        next_tick += SAMPLE_PERIOD_S
        sleep_for = next_tick - time.perf_counter()
        if sleep_for > 0:
            time.sleep(sleep_for)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    print("=" * 52)
    print("  200 Hz Micrometer + Encoder Logger")
    print("  S = Start   X = Stop   Q = Quit")
    print("=" * 52)

    # Init encoder (non-fatal)
    if not init_encoder():
        print("[Warning] Encoder not available — encoder column will be empty.")

    # Start micrometer thread
    mic_thread = threading.Thread(target=micrometer_thread, daemon=True)
    mic_thread.start()

    # Start logger thread
    log_thread = threading.Thread(target=logger_loop, daemon=True)
    log_thread.start()

    # Terminal control loop
    print("\nCommands: s = start,  x = stop,  q = quit\n")
    try:
        while state.running:
            # We removed the ">" string from input() so it plays nicely with \r line overwriting
            cmd = input().strip().lower() 
            if cmd == "s":
                with state.lock:
                    already = state.recording
                if already:
                    print("\n[Logger] Already recording.")
                else:
                    start_recording()
            elif cmd == "x":
                with state.lock:
                    already = state.recording
                if not already:
                    print("\n[Logger] Not currently recording.")
                else:
                    stop_recording()
            elif cmd == "q":
                print("\n[Logger] Quitting...")
                with state.lock:
                    if state.recording:
                        state.recording = False
                        if state.csv_file:
                            state.csv_file.close()
                state.running = False

    except KeyboardInterrupt:
        state.running = False

    finally:
        if encoder_device is not None:
            try:
                encoder_device.close()
            except Exception:
                pass
        print("Done.")


if __name__ == "__main__":
    main()