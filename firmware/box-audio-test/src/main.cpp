// Box-3 audio check, no Wi-Fi: beep (speaker works?), record 5 s, play it back (mic works?).
// Nothing plays at startup. Commands in the serial monitor (each followed by Enter):
//   b  beep only        t  full test (beep, record 5 s, play it back)
//   +  volume up 5      -  volume down 5   (these only change the setting; nothing plays)

#include <Arduino.h>

// The board file #defines these, which breaks the audio library's constants of the same name.
#undef ES8311_ADDR
#undef ES7210_ADDR
#include "AudioBoard.h"
#include "driver/i2s.h"

// ESP32-S3-BOX-3 pins, from Espressif's board support package (esp-bsp, esp-box-3.h).
// Not the "esp32s3box" defaults: on the Box-3 the audio word clock moved from GPIO 47 to 45.
constexpr int PIN_I2C_SCL = 18, PIN_I2C_SDA = 8;
constexpr int PIN_MCLK = 2, PIN_BCLK = 17, PIN_LRCK = 45, PIN_DOUT = 15, PIN_DIN = 16;
constexpr int PIN_SPEAKER_AMP = 46;

constexpr int RATE = 16000;  // what the backend's /box/reply/audio expects
constexpr int RECORD_S = 5;
constexpr size_t FRAMES = RATE * RECORD_S;
constexpr size_t CHUNK = 256;  // frames per I2S read/write
// Keep these low: with the amplifier on, the Box is very loud. Raise slowly.
int volume = 65;                // speaker chip volume, 0-100; change with + and -
constexpr int BEEP_LEVEL = 2000;  // tone strength, out of 32767
constexpr int PLAYBACK_PEAK = 4000;  // the recording is scaled so its loudest moment is this (~2x the beep)

DriverDeviceInfo pins;
// ES8311 drives the speaker, ES7210 reads the microphones; both are set up over I2C.
AudioBoard board(AudioDriverES8311_ES7210, pins);
int16_t* recording;  // stereo frames (left, right), in PSRAM: 5 s is 320 KB

bool startI2S() {
  // One I2S port both ways: the ESP32 sends the clocks, both chips follow them.
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX);
  cfg.sample_rate = RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = CHUNK;
  cfg.tx_desc_auto_clear = true;  // silence, not a repeated buzz, when we stop writing
  cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;

  i2s_pin_config_t p = {};
  p.mck_io_num = PIN_MCLK;
  p.bck_io_num = PIN_BCLK;
  p.ws_io_num = PIN_LRCK;
  p.data_out_num = PIN_DOUT;
  p.data_in_num = PIN_DIN;

  return i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) == ESP_OK && i2s_set_pin(I2S_NUM_0, &p) == ESP_OK;
}

bool startCodecs() {
  pins.addI2C(PinFunction::CODEC, PIN_I2C_SCL, PIN_I2C_SDA);
  pins.addI2S(PinFunction::CODEC, PIN_MCLK, PIN_BCLK, PIN_LRCK, PIN_DOUT, PIN_DIN);
  pins.addPin(PinFunction::PA, PIN_SPEAKER_AMP, PinLogic::Output);

  CodecConfig cfg;
  cfg.input_device = ADC_INPUT_ALL;
  cfg.output_device = DAC_OUTPUT_ALL;
  cfg.i2s.bits = BIT_LENGTH_16BITS;
  cfg.i2s.rate = RATE_16K;
  if (!board.begin(cfg)) return false;
  board.setVolume(volume);
  board.setInputVolume(80);
  board.setPAPower(true);  // the speaker amplifier is off until this pin goes high
  pinMode(PIN_SPEAKER_AMP, OUTPUT);  // and set it by hand too, in case the library didn't
  digitalWrite(PIN_SPEAKER_AMP, HIGH);
  return true;
}

// Is each chip answering on I2C? ES8311 also reports its chip ID (should be 0x83 0x11).
void checkChips() {
  for (uint8_t addr : {0x18, 0x40}) {
    Wire.beginTransmission(addr);
    Serial.printf("     chip 0x%02X (%s): %s\n", addr, addr == 0x18 ? "speaker ES8311" : "mic ES7210",
                  Wire.endTransmission() == 0 ? "answers" : "NO ANSWER");
  }
  auto reg = [](uint8_t r) {
    Wire.beginTransmission(0x18); Wire.write(r); Wire.endTransmission(false);
    Wire.requestFrom(0x18, 1); return Wire.available() ? Wire.read() : -1;
  };
  Serial.printf("     ES8311 id=0x%02X 0x%02X  volume reg=0x%02X  dac mute reg(0x31)=0x%02X  amp pin=%d\n",
                reg(0xFD), reg(0xFE), reg(0x32), reg(0x31), digitalRead(PIN_SPEAKER_AMP));
}

void writeFrames(const int16_t* frames, size_t count) {
  size_t written;
  i2s_write(I2S_NUM_0, frames, count * 2 * sizeof(int16_t), &written, portMAX_DELAY);
}

void beep() {
  Serial.println("1/3  BEEP: a short, quiet tone");
  static int16_t buf[CHUNK * 2];
  for (size_t done = 0; done < RATE / 2; done += CHUNK) {
    for (size_t i = 0; i < CHUNK; i++) {
      int16_t s = BEEP_LEVEL * sin(2 * PI * 440 * (done + i) / RATE);
      buf[2 * i] = buf[2 * i + 1] = s;
    }
    writeFrames(buf, CHUNK);
  }
}

// Returns which channel (0 left, 1 right) had the loudest sound.
int record() {
  // Drop ~0.2 s first: the buffers still hold sound from before (including the beep).
  static int16_t junk[CHUNK * 2];
  size_t got;
  for (int i = 0; i < RATE / 5 / CHUNK; i++) i2s_read(I2S_NUM_0, junk, sizeof(junk), &got, portMAX_DELAY);

  Serial.printf("2/3  RECORDING %d s: talk now\n", RECORD_S);
  for (size_t done = 0; done < FRAMES; done += CHUNK) {
    size_t n = min(CHUNK, FRAMES - done);  // the last chunk is shorter: don't write past the buffer
    i2s_read(I2S_NUM_0, recording + done * 2, n * 2 * sizeof(int16_t), &got, portMAX_DELAY);
  }

  // Sound is how much the signal moves around its average. A stuck or constant value can look
  // "loud" by its peak but plays back as silence, so measure the movement (RMS around the mean).
  double sum[2] = {0, 0}, sq[2] = {0, 0};
  for (size_t i = 0; i < FRAMES * 2; i++) sum[i % 2] += recording[i];
  double mean[2] = {sum[0] / FRAMES, sum[1] / FRAMES};
  for (size_t i = 0; i < FRAMES * 2; i++) sq[i % 2] += pow(recording[i] - mean[i % 2], 2);
  int rms[2] = {(int)sqrt(sq[0] / FRAMES), (int)sqrt(sq[1] / FRAMES)};
  // Out of 32767. Quiet room: tens. Talking near the Box: hundreds to thousands.
  Serial.printf("     mic sound  left=%d  right=%d   (average value left=%d right=%d)\n",
                rms[0], rms[1], (int)mean[0], (int)mean[1]);
  Serial.print("     raw samples (L,R):");
  for (int i = RATE; i < RATE + 8; i++) Serial.printf(" %d,%d", recording[2 * i], recording[2 * i + 1]);
  Serial.println();
  if (max(rms[0], rms[1]) < 30) Serial.println("     !! no sound in the recording: the mic data isn't arriving");
  return rms[1] > rms[0] ? 1 : 0;
}

void playBack(int channel) {
  Serial.println("3/3  PLAYBACK: you should hear what you just said");
  // Scale so the loudest moment is PLAYBACK_PEAK: a shout into the mic never plays back as a blast.
  int peak = 1;
  for (size_t i = 0; i < FRAMES; i++) peak = max(peak, abs((int)recording[i * 2 + channel]));
  float gain = min(1.0f, (float)PLAYBACK_PEAK / peak);
  static int16_t buf[CHUNK * 2];
  for (size_t done = 0; done < FRAMES; done += CHUNK) {
    size_t n = min(CHUNK, FRAMES - done);
    for (size_t i = 0; i < n; i++) buf[2 * i] = buf[2 * i + 1] = recording[(done + i) * 2 + channel] * gain;
    writeFrames(buf, n);
  }
}

void runTest() {
  beep();
  playBack(record());
  Serial.println("Done.");
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // give the serial monitor time to connect over USB
  Serial.println("\n=== Box-3 audio test ===");

  recording = (int16_t*)ps_malloc(FRAMES * 2 * sizeof(int16_t));
  if (!recording) Serial.println("!! no PSRAM: check the board setting");
  if (!startI2S()) Serial.println("!! I2S start failed");
  if (!startCodecs()) Serial.println("!! codec setup failed: the chips didn't answer over I2C");
  checkChips();
  // No test at startup, so plugging the Box in never makes a sound by surprise.
  Serial.printf("Ready. Volume %d. Commands: b = beep, t = full test, + / - = volume\n", volume);
}

void loop() {
  if (!Serial.available()) {
    delay(20);
    return;
  }
  char c = Serial.read();
  if (c == 'b') {
    beep();
    Serial.println("Done.");
  } else if (c == 't' && recording) {
    runTest();
  } else if (c == '+' || c == '-') {
    volume = constrain(volume + (c == '+' ? 5 : -5), 0, 100);
    board.setVolume(volume);
    Serial.printf("Volume %d (nothing played)\n", volume);
  }
}
