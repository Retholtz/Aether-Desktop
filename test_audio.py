from core.audio_stream import get_available_audio_devices

def list_audio_devices():
    res = get_available_audio_devices()
    inputs = res.get("inputs", [])
    outputs = res.get("outputs", [])

    print("\n" + "=" * 65)
    print("      AETHER DESKTOP - VERIFIED AVAILABLE AUDIO DEVICES")
    print("=" * 65)

    print("\n--- AVAILABLE MICROPHONES (INPUT) ---")
    if inputs:
        for dev in inputs:
            def_tag = " [DEFAULT]" if dev.get("is_default") else ""
            print(f"[{dev['index']:2d}] {dev['name']}{def_tag} | {dev['samplerate']}Hz | {dev['api']}")
    else:
        print("  None detected or available.")

    print("\n--- AVAILABLE SPEAKERS (OUTPUT) ---")
    if outputs:
        for dev in outputs:
            def_tag = " [DEFAULT]" if dev.get("is_default") else ""
            print(f"[{dev['index']:2d}] {dev['name']}{def_tag} | {dev['samplerate']}Hz | {dev['api']}")
    else:
        print("  None detected or available.")

    print("\n" + "=" * 65)

if __name__ == "__main__":
    list_audio_devices()
