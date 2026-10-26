// A streaming box-filter downsampler and 40ms PCM frame packer. No audio leaves this
// worklet except through the explicitly connected WebSocket client in app.js.
class VoiceHubCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.samples = new Int16Array(640);
    this.position = 0;
    this.meterSamples = 0;
    this.peak = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of input) {
      this.peak = Math.max(this.peak, Math.abs(value));
      if (++this.meterSamples >= sampleRate / 4) {
        this.port.postMessage({type: "level", peak: this.peak});
        this.meterSamples = 0;
        this.peak = 0;
      }
      this.sum += value;
      this.count++;
      this.phase += 16000;
      while (this.phase >= sampleRate) {
        this.phase -= sampleRate;
        const sample = this.count ? this.sum / this.count : value;
        this.sum = 0;
        this.count = 0;
        this.samples[this.position++] = Math.round(
          Math.max(-1, Math.min(1, sample)) * 32767,
        );
        if (this.position === 640) {
          this.port.postMessage(this.samples.buffer, [this.samples.buffer]);
          this.samples = new Int16Array(640);
          this.position = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("voicehub-capture", VoiceHubCapture);
