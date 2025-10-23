import openmeteo_requests
from datetime import datetime
import pandas as pd
import requests_cache
from retry_requests import retry
import math
import pvlib

# Setup the Open-Meteo API client with cache and retry on error
cache_session = requests_cache.CachedSession('.cache', expire_after = 3600)
retry_session = retry(cache_session, retries = 5, backoff_factor = 0.2)
openmeteo = openmeteo_requests.Client(session = retry_session)


# Make sure all required weather variables are listed here
# The order of variables in hourly or daily is important to assign them correctly below
url = "https://api.open-meteo.com/v1/forecast"
params = {
	"latitude": 13.754,
	"longitude": 100.5014,
	"models": "best_match",
	"minutely_15": ["temperature_2m", "relative_humidity_2m", "shortwave_radiation_instant", "diffuse_radiation_instant", "direct_normal_irradiance_instant", "wind_speed_80m", "wind_direction_80m", "rain"],
	"timezone": "Asia/Bangkok",
	"forecast_days": 1,
}
responses = openmeteo.weather_api(url, params=params)


# Process first location. Add a for-loop for multiple locations or weather models
response = responses[0]
print(f"Coordinates: {response.Latitude()}°N {response.Longitude()}°E")
print(f"Elevation: {response.Elevation()} m asl")
print(f"Timezone: {response.Timezone()}{response.TimezoneAbbreviation()}")
print(f"Timezone difference to GMT+0: {response.UtcOffsetSeconds()}s")

# Process hourly data. The order of variables needs to be the same as requested.
minutely_15 = response.Minutely15()
minutely_15_temperature_2m = minutely_15.Variables(0).ValuesAsNumpy()
minutely_15_relative_humidity_2m = minutely_15.Variables(1).ValuesAsNumpy()
minutely_15_shortwave_radiation_instant = minutely_15.Variables(2).ValuesAsNumpy()
minutely_15_diffuse_radiation_instant = minutely_15.Variables(3).ValuesAsNumpy()
minutely_15_direct_normal_irradiance_instant = minutely_15.Variables(4).ValuesAsNumpy()
minutely_15_wind_speed_80m = minutely_15.Variables(5).ValuesAsNumpy()
minutely_15_wind_direction_80m = minutely_15.Variables(6).ValuesAsNumpy()
minutely_15_rain = minutely_15.Variables(7).ValuesAsNumpy()


# สร้างช่วงเวลาจาก API (ยังเป็น UTC)
minutely_15_data = {"date": pd.date_range(
	start = pd.to_datetime(minutely_15.Time(), unit = "s", utc = True),
	end = pd.to_datetime(minutely_15.TimeEnd(), unit = "s", utc = True),
	freq = pd.Timedelta(seconds = minutely_15.Interval()),
	inclusive = "left"
)}

# แปลงเป็นเวลาไทย
date_bangkok = minutely_15_data["date"].tz_convert("Asia/Bangkok")

# เพิ่มเข้า dict
minutely_15_data = {"date": date_bangkok}



minutely_15_data["temperature_2m"] = minutely_15_temperature_2m
minutely_15_data["relative_humidity_2m"] = minutely_15_relative_humidity_2m
minutely_15_data["shortwave_radiation_instant"] = minutely_15_shortwave_radiation_instant
minutely_15_data["diffuse_radiation_instant"] = minutely_15_diffuse_radiation_instant
minutely_15_data["direct_normal_irradiance_instant"] = minutely_15_direct_normal_irradiance_instant
minutely_15_data["wind_speed_80m"] = minutely_15_wind_speed_80m
minutely_15_data["wind_direction_80m"] = minutely_15_wind_direction_80m
minutely_15_data["rain"] = minutely_15_rain


minutely_15_data= pd.DataFrame(data = minutely_15_data)
print("\nHourly data\n", minutely_15_data)

# --- แก้ timezone: ลบ timezone ออกจากคอลัมน์วันที่ (Excel จะได้ไม่ error)
minutely_15_data["date"] = minutely_15_data["date"].dt.tz_localize(None)


# สร้างชื่อไฟล์โดยใช้วันเวลา ณ ตอนนั้น
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
output_filename = f"run_{timestamp}.xlsx"


# บันทึกเป็น Excel
minutely_15_data.to_excel(output_filename, index=False)

print(f"\n✅ บันทึกข้อมูลเรียบร้อยเป็นไฟล์ Excel: {output_filename}")


# ------------------------------
# ฟังก์ชันคำนวณ Solar PV Output
# ------------------------------
def solar_pv_output_row(DNI, DHI, GHI, T_amb, alpha, gamma_s,
                        beta=10, gamma_p=180, rho=0.2,
                        NOCT=45, gamma=-0.004,
                        eta_STC=0.18, A_module=1.7,
                        N=30, P_rated=430,
                        eta_inv=0.96, f_loss=0.95):
    
    # --- AOI ---
    cos_ti = (math.sin(math.radians(alpha)) * math.cos(math.radians(beta)) *
              math.cos(math.radians(gamma_s - gamma_p)) +
              math.cos(math.radians(alpha)) * math.sin(math.radians(beta)))
    cos_ti = max(0, cos_ti)

    # --- POA ---
    B_POA = DNI * cos_ti
    D_POA = DHI * (1 + math.cos(math.radians(beta))) / 2
    R_POA = rho * GHI * (1 - math.cos(math.radians(beta))) / 2
    POA = B_POA + D_POA + R_POA

    # --- Cell Temperature ---
    T_cell = T_amb + (NOCT - 20) / 800 * POA

    # --- Temp factor ---
    f_T = 1 + gamma * (T_cell - 25)

    # --- DC Power (จำนวนแผง) ---
    P_DC = N * P_rated * (POA / 1000) * f_T

    # --- AC Power ---
    P_AC = P_DC * eta_inv * f_loss

    return POA, T_cell, P_DC, P_AC

# ------------------------------
# รวมข้อมูลจาก Open-Meteo
# ------------------------------
df = pd.DataFrame({
    "date": date_bangkok,
    "Temp": minutely_15_temperature_2m,
    "GHI": minutely_15_shortwave_radiation_instant,
    "DHI": minutely_15_diffuse_radiation_instant,
    "DNI": minutely_15_direct_normal_irradiance_instant,
})

# ------------------------------
# คำนวณ Solar Position (ใช้ pvlib แนะนำ)
# ------------------------------
solpos = pvlib.solarposition.get_solarposition(df['date'], 13.754, 100.5014)

df['solar_altitude'] = 90 - solpos['apparent_zenith']   # alpha
df['solar_azimuth'] = solpos['azimuth']                 # gamma_s

# ------------------------------
# Loop คำนวณผลลัพธ์
# ------------------------------
results = []
for i, row in df.iterrows():
    POA, Tcell, P_DC, P_AC = solar_pv_output_row(
        row['DNI'], row['DHI'], row['GHI'], row['Temp'],
        row['solar_altitude'], row['solar_azimuth']
    )
    results.append([POA, Tcell, P_DC, P_AC])

df[['POA', 'Tcell', 'P_DC', 'P_AC']] = results

# ------------------------------
# Export Excel (fix timezone issue)
# ------------------------------
df['date'] = df['date'].dt.tz_localize(None)   # <-- ทำให้ timezone-naive

output_filename = f"Bangkok_Solar_Output_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx"
df.to_excel(output_filename, index=False)

print(f"✅ บันทึกผลลัพธ์เป็นไฟล์ {output_filename}")

