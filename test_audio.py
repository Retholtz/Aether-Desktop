import sounddevice as sd

def list_audio_devices():
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    
    print("\n" + "=" * 60)
    print("        AETHER DESKTOP - AUDIO HARDWARE ENUMERATION")
    print("=" * 60)
    
    print("\n--- AVAILABLE INPUT MICROPHONES ---")
    for idx, dev in enumerate(devices):
        if dev['max_input_channels'] > 0:
            api_name = hostapis[dev['hostapi']]['name']
            # Highlight Windows Core Audio (WASAPI/DirectSound)
            print(f"[{idx}] {dev['name']} ({api_name}) - Channels: {dev['max_input_channels']}, Default Sample Rate: {int(dev['default_samplerate'])}Hz")
            
    print("\n--- AVAILABLE OUTPUT SPEAKERS ---")
    for idx, dev in enumerate(devices):
        if dev['max_output_channels'] > 0:
            api_name = hostapis[dev['hostapi']]['name']
            print(f"[{idx}] {dev['name']} ({api_name}) - Channels: {dev['max_output_channels']}, Default Sample Rate: {int(dev['default_samplerate'])}Hz")
            
    print("\n" + "=" * 60)

if __name__ == "__main__":
    list_audio_devices()