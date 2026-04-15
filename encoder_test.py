import time
import sys

try:
    from Phidget22.Devices.Encoder import Encoder
    from Phidget22.PhidgetException import PhidgetException
except ImportError:
    raise SystemExit("Install 'phidget22': pip install phidget22")

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
ENCODER_HUB_PORT    = 0
ENCODER_IS_HUB_PORT = False  # False if using a VINT device like ENC1001
ENCODER_CHANNEL     = 0

def main():
    print("=== Phidget Encoder Test Program ===")
    
    enc = None
    try:
        # 1. Create and configure the encoder
        enc = Encoder()
        enc.setHubPort(ENCODER_HUB_PORT)
        enc.setIsHubPortDevice(ENCODER_IS_HUB_PORT)
        enc.setChannel(ENCODER_CHANNEL)
        
        # 2. Open and wait for attachment
        print("Waiting for encoder to attach (up to 5 seconds)...")
        enc.openWaitForAttachment(5000)
        
        # 3. Optimize data interval
        min_di = enc.getMinDataInterval()
        enc.setDataInterval(min_di)
        print(f"Attached successfully! Min data interval = {min_di} ms.")
        print("Press Ctrl+C to exit.\n")
        
        # 4. Polling loop (runs at ~10 Hz for clean terminal output)
        while True:
            try:
                # Read the current position directly
                counts = enc.getPosition()
                
                # Print using \r to overwrite the same line (prevents terminal spam)
                sys.stdout.write(f"\rLive Encoder Counts: {counts:>8}")
                sys.stdout.flush()
                
            except PhidgetException:
                sys.stdout.write("\r[Error] Could not read encoder position.  ")
                sys.stdout.flush()
            
            time.sleep(0.1)

    except PhidgetException as e:
        print(f"\n[Phidget Error] Failed to attach or configure: {e}")
    except KeyboardInterrupt:
        print("\n\nTest stopped by user.")
    finally:
        # 5. Clean up connection
        if enc is not None:
            try:
                enc.close()
                print("Encoder connection closed.")
            except PhidgetException:
                pass

if __name__ == "__main__":
    main()