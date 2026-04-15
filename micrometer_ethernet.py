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
        # Disable Nagle's algorithm to allow 1000Hz+ speeds
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
        
        # Note: Handshake 2 triggers a massive ~5KB metadata reply from the controller. 
        # We need to loop slightly to clear the socket buffer so it doesn't bleed into our polling loop.
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
                
                # 2. Grab the response (Expected length based on Wireshark: 72 bytes)
                data = recv(1024)
                
                if data:
                    # Print the raw hex data with spaces between bytes
                    print(f"Length {len(data)} | Data: {data.hex(' ')}")
                    
                    # --- DECODING THE FLOAT ---
                    # Once you run this script, look at the hex output.
                    # Find the 4-byte chunk that changes as your measurement changes.
                    # Example: If your measurement is located at byte indices 32 through 35,
                    # uncomment the lines below to decode it into a real Python number:
                    #
                    # val = struct.unpack('<f', data[32:36])[0] 
                    # print(val)
                    
        except KeyboardInterrupt:
            print("\nPolling stopped by user.")
        except Exception as e:
            print(f"\nERROR: {e}")

if __name__ == "__main__":
    fast_binary_poll()