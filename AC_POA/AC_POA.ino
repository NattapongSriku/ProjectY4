#include <Arduino.h>
#include <IRremoteESP8266.h>
#include <IRsend.h>
#include <ir_Mitsubishi.h>
bool jsonReady = false;
// ===== JSON payload buffer =====
String lastPayload = "";
bool hasPayload = false;

const uint16_t kIrLed = 4;  // ESP8266 GPIO pin to use. Recommended: 4 (D2).
IRMitsubishiAC ac(kIrLed);  // Set the GPIO used for sending messages.

// ======================
// APPLY AC STATE (FROM JSON)
// ======================
void applyACStateFromPayload(String payload) {{
  Serial.println("📥 JSON payload received:");
  Serial.println(payload);

  jsonReady = true;   // 👈 จุดนี้คือหลักฐานว่า JSON มาแล้ว
}
  // OFF case
  if (payload == "OFF") {
    ac.off();
    Serial.println("AC OFF (from JSON)");
    ac.send();
    return;
  }

  // Expected format: ON,26,2
  int c1 = payload.indexOf(',');
  int c2 = payload.lastIndexOf(',');

  if (c1 < 0 || c2 < 0) return;

  int temp = payload.substring(c1 + 1, c2).toInt();
  int fan  = payload.substring(c2 + 1).toInt();

  ac.on();
  ac.setMode(kMitsubishiAcCool);
  ac.setTemp(temp);
  ac.setFan(fan);
  ac.setVane(kMitsubishiAcVaneAuto);

  Serial.print("AC SET FROM JSON -> Temp=");
  Serial.print(temp);
  Serial.print(" Fan=");
  Serial.println(fan);

  ac.send();
}

// ======================
// READ AC STATE FROM SERIAL
// ======================
void readACStateFromSerial() {
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();

  // Expected:
  // AC:ON,26,2
  // AC:OFF
  if (line.startsWith("AC:")) {
    String payload = line.substring(3);
    applyACStateFromPayload(payload);
  }
}

// ======================
// DEBUG STATE (UNCHANGED)
// ======================
void printState() {
  Serial.println("Mitsubishi A/C remote is in the following state:");
  Serial.printf("  %s\n", ac.toString().c_str());

  unsigned char* ir_code = ac.getRaw();
  Serial.print("IR Code: 0x");
  for (uint8_t i = 0; i < kMitsubishiACStateLength; i++)
    Serial.printf("%02X", ir_code[i]);
  Serial.println();
}

// ======================
// SETUP (UNCHANGED)
// ======================
void setup() {
  ac.begin();
  Serial.begin(115200);
  delay(200);

  Serial.println("Default state of the remote.");
  printState();

  Serial.println("Setting desired state for A/C.");
  ac.on();
  ac.setFan(2);
  ac.setMode(kMitsubishiAcCool);
  ac.setTemp(25);
  ac.setVane(kMitsubishiAcVaneAuto);
}

// ======================
// LOOP (MINIMAL CHANGE)
// ======================
void loop() {

  // รับ state จาก JSON (ผ่าน Python)
  readACStateFromSerial();

  if (!Serial.available()) return;

  char c = Serial.read();
  bool needSend = true;

  switch (c) {

    case '+': {
      int t = ac.getTemp();
      if (t < 30) t++;
      ac.setTemp(t);
      Serial.printf("Temp = %d C\n", t);
      break;
    }

    case '-': {
      int t = ac.getTemp();
      if (t > 16) t--;
      ac.setTemp(t);
      Serial.printf("Temp = %d C\n", t);
      break;
    }

    case '1':
      ac.setFan(1);
      Serial.println("Fan = 1 (Low)");
      break;

    case '2':
      ac.setFan(2);
      Serial.println("Fan = 2 (Medium)");
      break;

    case '3':
      ac.setFan(3);
      Serial.println("Fan = 3 (High)");
      break;

    // ===== APPLY STATE FROM JSON =====
    
    case 'A':
      if (!jsonReady) {
      Serial.println("⚠️ JSON NOT received yet");
      needSend = false;
      break;
      }
      Serial.println("✅ Apply AC state from JSON");
      applyACStateFromPayload(lastPayload);
      break;
    case '0':
      ac.off();
      Serial.println("AC = Off");
      break;

    default:
      needSend = false;
      break;
  }

  if (needSend) {
    ac.send();
    printState();
  }
}
