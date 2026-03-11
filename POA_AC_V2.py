# ============================================================
# AC Controller: Auto (15-min Loop) & Manual Mode
# Includes: POA Calculation, Night-hold logic, and Serial Link
# ============================================================

import math
import json
from datetime import datetime
import pandas as pd
import pvlib
import numpy as np
import openmeteo_requests
import requests_cache
from retry_requests import retry
import serial
import time

# =========================
# 1. CONFIGURATION
# =========================
LATITUDE = 13.754
LONGITUDE = 100.5014
TIMEZONE = "Asia/Bangkok"

# PV Configuration
TILT = 10 
AZIMUTH_PANEL = 180 
ALBEDO = 0.2

# Control thresholds (W/m²)
POA_THRESHOLDS = {
    "OFF": 50,
    "LOW": 300,
    "MID": 600,
    "HIGH": 800,
}

SERIAL_PORT = 'COM8'  # ตรวจสอบ Port ESP32
BAUD_RATE = 115200
STATE_FILE = "ac_state.json"

# =========================
# 2. API CLIENT (CACHED)
# =========================
cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

# =========================
# 3. CORE CALCULATIONS
# =========================
def calc_poa(DNI, DHI, GHI, solar_altitude, solar_azimuth,
             beta=TILT, gamma_p=AZIMUTH_PANEL, rho=ALBEDO):
    
    # คำนวณมุมตกกระทบ (Incidence Angle)
    cos_ti = (
        math.sin(math.radians(solar_altitude)) *
        math.cos(math.radians(beta)) *
        math.cos(math.radians(solar_azimuth - gamma_p)) +
        math.cos(math.radians(solar_altitude)) *
        math.sin(math.radians(beta))
    )
    cos_ti = max(0, cos_ti)

    B_POA = DNI * cos_ti
    D_POA = DHI * (1 + math.cos(math.radians(beta))) / 2
    R_POA = rho * GHI * (1 - math.cos(math.radians(beta))) / 2

    return B_POA + D_POA + R_POA

def get_latest_poa():
    """Fetch API and return latest POA + ambient temp"""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "minutely_15": [
                "temperature_2m",
                "shortwave_radiation_instant",
                "diffuse_radiation_instant",
                "direct_normal_irradiance_instant",
            ],
            "timezone": TIMEZONE,
            "forecast_days": 1,
        }

        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        min15 = response.Minutely15()

        temp_values = min15.Variables(0).ValuesAsNumpy()
        ghi_values = min15.Variables(1).ValuesAsNumpy()
        dhi_values = min15.Variables(2).ValuesAsNumpy()
        dni_values = min15.Variables(3).ValuesAsNumpy()

        dates = pd.date_range(
            start=pd.to_datetime(min15.Time(), unit="s", utc=True),
            periods=len(temp_values),
            freq=pd.Timedelta(seconds=min15.Interval())
        ).tz_convert(TIMEZONE)

        df = pd.DataFrame({
            "date": dates,
            "Temp": temp_values,
            "GHI": ghi_values,
            "DHI": dhi_values,
            "DNI": dni_values,
        })

        solpos = pvlib.solarposition.get_solarposition(df["date"], LATITUDE, LONGITUDE)
        df["solar_altitude"] = 90 - solpos["apparent_zenith"].values
        df["solar_azimuth"] = solpos["azimuth"].values

        df["POA"] = df.apply(lambda r: calc_poa(r.DNI, r.DHI, r.GHI, r.solar_altitude, r.solar_azimuth), axis=1)

        now = pd.Timestamp.now(tz=TIMEZONE)
        past_df = df[df["date"] <= now]
        latest = past_df.iloc[-1] if not past_df.empty else df.iloc[0]

        return {
            "time": latest["date"],
            "poa": float(latest["POA"]),
            "temp": float(latest["Temp"]),
        }
    except Exception as e:
        print(f"❌ API Error: {e}")
        return {"poa": 0, "temp": 0}

def decide_ac_state_auto(poa):
    """Logic สำหรับโหมด Auto ตามค่า POA"""
    if poa < POA_THRESHOLDS["OFF"]:
        return {"power": "OFF", "temp": 27, "fan": 1}
    elif poa < POA_THRESHOLDS["LOW"]:
        return {"power": "ON", "temp": 26, "fan": 1}
    elif poa < POA_THRESHOLDS["MID"]:
        return {"power": "ON", "temp": 25, "fan": 2}
    elif poa < POA_THRESHOLDS["HIGH"]:
        return {"power": "ON", "temp": 24, "fan": 2}
    else:
        return {"power": "ON", "temp": 24, "fan": 3}

# =========================
# 4. SERIAL COMMUNICATION
# =========================
def send_to_esp(ac_state):
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        time.sleep(2) # รอ Serial Reboot

        json_payload = json.dumps(ac_state)
        ser.write((json_payload + '\n').encode('utf-8'))
        
        print(f"📡 Sent to ESP32: {json_payload}")
        
        time.sleep(1)
        if ser.in_waiting > 0:
            resp = ser.read_all().decode('utf-8', errors='ignore').strip()
            print(f"✅ ESP32 Response: {resp}")

        ser.close()
    except Exception as e:
        print(f"❌ Serial Error: {e}")

# =========================
# 5. MAIN EXECUTION (LOOP)
# =========================
def main():
    print("======================================")
    print("   AC SMART CONTROLLER INITIALIZED   ")
    print("======================================")
    print("Modes: [A] Auto (15-min Loop) | [M] Manual (Once)")
    
    user_mode = input("Select Mode (A/M): ").strip().upper()

    if user_mode == 'M':
        # --- MANUAL MODE ---
        print("\n[MANUAL MODE SETTING]")
        pwr = input("Power (ON/OFF): ").strip().upper()
        tmp = int(input("Temperature (16-30): "))
        fan = int(input("Fan Speed (1-3): "))
        
        payload = {
            "mode": "M",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "power": pwr,
            "temp": tmp,
            "fan": fan
        }
        send_to_esp(payload)
        print("🎯 Manual Command Sent. Program Finished.")
        
    elif user_mode == 'A':
        # --- AUTO MODE LOOP ---
        print(f"\n🚀 AUTO MODE ACTIVATED (Interval: 00, 15, 30, 45 min)")
        print("Checking POA every 15 minutes. Night-time commands will be skipped.")
        
        while True:
            now = datetime.now()
            
            # เช็กเวลาให้ตรงนาทีที่กำหนด (00, 15, 30, 45) และวินาทีที่ 0
            if now.minute in [0, 15, 30, 45] and now.second == 0:
                print(f"\n⏰ Time Check: {now.strftime('%H:%M:%S')}")
                
                solar_data = get_latest_poa()
                poa_val = solar_data["poa"]
                
                # เงื่อนไข: ถ้ากลางคืน (POA <= 0) ไม่ต้องส่งคำสั่งใหม่ ให้ค้างค่าเดิมไว้
                if poa_val <= 0:
                    print(f"🌙 Night Mode Detected (POA: {poa_val:.2f}). No command sent.")
                else:
                    ac_cmd = decide_ac_state_auto(poa_val)
                    full_payload = {
                        "mode": "A",
                        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "poa": round(poa_val, 2),
                        "ambient_temp": round(solar_data["temp"], 2),
                        **ac_cmd
                    }
                    
                    send_to_esp(full_payload)
                    
                    # บันทึกสถานะล่าสุดลงไฟล์
                    with open(STATE_FILE, "w") as f:
                        json.dump(full_payload, f, indent=2)
                
                # หน่วงเวลา 1 วินาทีเพื่อไม่ให้หลุดลูปซ้ำในวินาทีเดียวกัน
                time.sleep(1)
            
            # เช็กเวลาทุกๆ 0.5 วินาที (ไม่กิน CPU)
            time.sleep(0.5)
            
    else:
        print("❌ Invalid input. Please restart and choose A or M.")

if __name__ == "__main__":
    main()