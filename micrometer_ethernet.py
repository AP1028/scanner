import socket
import struct

# --- Configuration ---
IP_ADDRESS = '10.0.0.3'
TCP_PORT = 24683

# --- Extracted Wireshark Binary Sequences ---
# 1. First handshake packet
HANDSHAKE_1 = bytes.fromhex("10 00 00 00 02 00 f0 00 00 00 00 00 04 00 00 00 37 00 00 00")

# 2. Second handshake packet (Requests program metadata)
HANDSHAKE_2 = bytes.fromhex("18 00 00 00 02 00 f0 00 00 00 00 00 0c 00 00 00 31 00 00 00 01 00 00 00 10 ff 00 00")

# 3. High-speed polling request
POLL_CMD = bytes.fromhex("14 00 00 00 02 00 f0 00 00 00 00 00 08 00 00 00 41 00 00 00 00 00 00 00")


def fast_binary_poll():
    print(f"Connecting to {IP_ADDRESS}:{TCP_PORT}...")
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Disable Nagle's algorithm for 1000Hz+ speeds
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) 
        s.settimeout(2.0)
        
        try:
            s.connect((IP_ADDRESS, TCP_PORT))
        except Exception as e:
            print(f"Failed to connect: {e}")
            return
            
        print("Connected! Starting initialization handshakes...")
        
        # --- HANDSHAKE SEQUENCE ---
        
        # Send Handshake 1
        s.sendall(HANDSHAKE_1)
        resp1 = s.recv(1024)
        
        # Send Handshake 2
        s.sendall(HANDSHAKE_2)
        
        # Clear the initial massive metadata buffer
        print("Clearing initial metadata buffer...")
        s.settimeout(0.5)
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk: 
                    break
            except socket.timeout:
                break # Buffer is completely empty when it times out
        
        print("Handshake complete. Starting high-speed polling loop. Press Ctrl+C to stop.\n")
        
        # --- HIGH SPEED POLLING LOOP ---
        s.settimeout(1.0)
        
        # Pre-allocate variable scopes to save microseconds
        sendall = s.sendall
        recv = s.recv
        
        try:
            while True:
                # 1. Fire the polling command
                sendall(POLL_CMD)
                
                # 2. Grab the response
                data = recv(1024)
                
                if data:
                    # 3. Only process the 72-byte data packets (ignore 4-byte keep-alives)
                    if len(data) == 72:
                        
                        # Extract the 4 bytes containing the measurement (Index 64 to 67)
                        raw_bytes = data[64:68]
                        
                        # Unpack as a little-endian signed 32-bit integer ('<i')
                        raw_int = struct.unpack('<i', raw_bytes)[0]
                        
                        # Scale it back to millimeters (1 unit = 0.0001 mm)
                        measurement_mm = raw_int * 0.0001
                        
                        # Print the clean output
                        print(f"Measurement: {measurement_mm:.4f} mm")
                        
        except KeyboardInterrupt:
            print("\nPolling stopped by user.")
        except Exception as e:
            print(f"\nERROR: {e}")

if __name__ == "__main__":
    fast_binary_poll()