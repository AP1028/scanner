from gpiozero import PWMOutputDevice, DigitalOutputDevice
from time import sleep

# 1. Setup the Enable Pins (Using BCM numbering)
# Physical Pin 11 is BCM 17
# Physical Pin 13 is BCM 27
r_en = DigitalOutputDevice(17) 
l_en = DigitalOutputDevice(27)  

# 2. Setup the PWM Pins (Using BCM numbering)
# Physical Pin 32 is BCM 12
# Physical Pin 33 is BCM 13
rpwm = PWMOutputDevice(12)   
lpwm = PWMOutputDevice(13)   

def main():
    # Turn BOTH enables High to activate the driver
    print("Activating motor driver...")
    r_en.on()
    l_en.on()

    try:
        # Move Forward
        print("Spinning Forward at 60% speed...")
        lpwm.value = 0.0      # Lock the opposite direction to 0
        rpwm.value = 0.1      # Set speed to 60%
        sleep(3)

        # Stop
        print("Stopping...")
        rpwm.value = 0.0
        sleep(2)

        # Move Reverse
        print("Spinning in Reverse at 60% speed...")
        rpwm.value = 0.0      # Lock the opposite direction to 0
        lpwm.value = 0.1      # Set speed to 60%
        sleep(3)

    except KeyboardInterrupt:
        # Allows you to exit cleanly by pressing Ctrl+C
        print("\nTest interrupted by user.")

    finally:
        # 3. Cleanup and Safety Shutdown
        print("Shutting down and disabling driver.")
        rpwm.off()
        lpwm.off()
        r_en.off()
        l_en.off()

if __name__ == "__main__":
    main()