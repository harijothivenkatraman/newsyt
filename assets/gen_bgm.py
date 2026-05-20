import numpy as np
import wave, os

os.makedirs('assets', exist_ok=True)

sample_rate = 44100
duration = 60  # 60-second loop

t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

# Low news broadcast bass thump every beat
bass = np.sin(2 * np.pi * 80 * t) * np.exp(-8 * (t % 0.5)) * 0.6
# Subtle synth arpeggio
arp_freqs = [440, 554, 659, 880]
arp = sum(np.sin(2 * np.pi * f * t) * np.exp(-4 * (t % 0.25)) * 0.08 for f in arp_freqs)
# High shimmery pad
pad = np.sin(2 * np.pi * 220 * t) * 0.04

mix = bass + arp + pad
mix = mix / np.max(np.abs(mix)) * 0.25

# Write stereo WAV
stereo = np.column_stack([mix, mix])
stereo_int16 = (stereo * 32767).astype(np.int16)

with wave.open('assets/bgm.wav', 'w') as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(sample_rate)
    wf.writeframes(stereo_int16.tobytes())

size_kb = os.path.getsize('assets/bgm.wav') / 1024
print(f'BGM generated: {size_kb:.0f} KB, {duration}s, 44100Hz stereo')
