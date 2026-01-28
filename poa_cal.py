# ============================================================
# AC Auto Controller based on POA Irradiance (W/m²)
# - Fetch weather API
# - Calculate POA
# - Decide AC state (Auto mode)
# - Save logical AC state for dashboard (Streamlit)
# ============================================================

import math
import json
from datetime import datetime

import pandas as pd
import pvlib
import openmeteo_requests
import requests_cache
from retry_requests import retry


# =========================
# 1. CONFIGURATION
# =========================
LATITUDE = 13.754
LONGITUDE = 100.5014
TIMEZONE = "Asia/Bangkok"

# PV / Panel
TILT = 10               # deg
AZIMUTH_PANEL = 180     # south
ALBEDO = 0.2

# Control thresholds (W/m²)
POA_THRESHOLDS = {
    "OFF": 200,
    "LOW": 400,
    "MID": 700,
}

SEND_INTERVAL_SEC = 15 * 60   # update AC every 15 minutes

STATE_FILE = "ac_state.json"


# =========================
# 2. API CLIENT (CACHED)
# =========================
cache_session = requests_cache.CachedSession(
    ".cache", expire_after=3600
)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)


# =========================
# 3. POA CALCULATION
# =========================
def calc_poa(DNI, DHI, GHI, solar_altitude, solar_azimuth,
             beta=TILT, gamma_p=AZIMUTH_PANEL, rho=ALBEDO):

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

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "models": "best_match",
        "minutely_15": [
            "temperature_2m",
            "shortwave_radiation_instant",
            "diffuse_radiation_instant",
            "direct_normal_irradiance_instant",
        ],
        "timezone": TIMEZONE,
        "forecast_days": 1,
    }

    response = openmeteo.weather_api(url, params=params)[0]
    min15 = response.Minutely15()

    T_amb = min15.Variables(0).ValuesAsNumpy()
    GHI   = min15.Variables(1).ValuesAsNumpy()
    DHI   = min15.Variables(2).ValuesAsNumpy()
    DNI   = min15.Variables(3).ValuesAsNumpy()

    date = pd.date_range(
        start=pd.to_datetime(min15.Time(), unit="s", utc=True),
        end=pd.to_datetime(min15.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=min15.Interval()),
        inclusive="left",
    ).tz_convert(TIMEZONE)

    df = pd.DataFrame({
        "date": date,
        "Temp": T_amb,
        "GHI": GHI,
        "DHI": DHI,
        "DNI": DNI,
    })

    solpos = pvlib.solarposition.get_solarposition(
        df["date"], latitude=LATITUDE, longitude=LONGITUDE
    )

    df["solar_altitude"] = 90 - solpos["apparent_zenith"]
    df["solar_azimuth"] = solpos["azimuth"]

    df["POA"] = [
        calc_poa(
            row.DNI,
            row.DHI,
            row.GHI,
            row.solar_altitude,
            row.solar_azimuth
        )
        for _, row in df.iterrows()
    ]

    latest = df.iloc[-1]

    return {
        "time": latest["date"],
        "poa": float(latest["POA"]),
        "temp": float(latest["Temp"]),
    }


# =========================
# 4. AC CONTROL LOGIC
# =========================
def decide_ac_state(poa):
    """Rule-based auto AC control"""

    if poa < POA_THRESHOLDS["OFF"]:
        return {
            "power": "OFF",
            "temp": None,
            "fan": None,
        }

    elif poa < POA_THRESHOLDS["LOW"]:
        return {
            "power": "ON",
            "temp": 28,
            "fan": 1,
        }

    elif poa < POA_THRESHOLDS["MID"]:
        return {
            "power": "ON",
            "temp": 26,
            "fan": 2,
        }

    else:
        return {
            "power": "ON",
            "temp": 24,
            "fan": 3,
        }


# =========================
# 5. ESP32 ACTUATION (PLACEHOLDER)
# =========================
def send_to_esp(ac_state):
    """
    Send command to ESP32
    (replace with serial / HTTP / MQTT)
    """
    print(">>> Send to ESP32:", ac_state)


# =========================
# 6. STATE MANAGEMENT
# =========================
def save_state(ac_state):
    with open(STATE_FILE, "w") as f:
        json.dump(ac_state, f, indent=2, default=str)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# =========================
# 7. MAIN LOOP (SINGLE RUN)
# =========================
def main():
    solar = get_latest_poa()
    poa = solar["poa"]

    ac_cmd = decide_ac_state(poa)

    ac_state = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "AUTO",
        "poa": poa,
        "ambient_temp": solar["temp"],
        **ac_cmd,
    }

    send_to_esp(ac_state)
    save_state(ac_state)

    print("AC state updated:")
    print(ac_state)


# =========================
# ENTRY POINT
# =========================
if __name__ == "__main__":
    main()
