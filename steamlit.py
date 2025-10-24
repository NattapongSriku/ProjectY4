# ===============================
# ☀️ Solar Power Dashboard (Bangkok)
# ===============================
import streamlit as st
import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry
from datetime import datetime
import math
import numpy as np
import pvlib

# -------------------------------
# 🔧 ดึงข้อมูลและคำนวณ
# -------------------------------
@st.cache_data(ttl=900)
def fetch_and_calculate():
    LAT, LON = 13.754, 100.5014  # พิกัดกรุงเทพฯ

    # Setup Open-Meteo API
    cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "models": "best_match",
        "minutely_15": [
            "temperature_2m", "relative_humidity_2m",
            "shortwave_radiation_instant", "diffuse_radiation_instant",
            "direct_normal_irradiance_instant",
            "wind_speed_10m", "wind_direction_10m", "rain"
        ],
        "timezone": "Asia/Bangkok",
        "forecast_days": 1,
    }

    responses = openmeteo.weather_api(url, params=params)
    response = responses[0]
    m15 = response.Minutely15()

    # สร้าง DataFrame จาก API
    df = pd.DataFrame({
        "date": pd.date_range(
            start=pd.to_datetime(m15.Time(), unit="s", utc=True),
            end=pd.to_datetime(m15.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=m15.Interval()),
            inclusive="left"
        ).tz_convert("Asia/Bangkok"),
        "Temp": m15.Variables(0).ValuesAsNumpy(),
        "RH": m15.Variables(1).ValuesAsNumpy(),
        "GHI": m15.Variables(2).ValuesAsNumpy(),
        "DHI": m15.Variables(3).ValuesAsNumpy(),
        "DNI": m15.Variables(4).ValuesAsNumpy(),
        "Wind_Spd": m15.Variables(5).ValuesAsNumpy(),
        "Wind_Dir": m15.Variables(6).ValuesAsNumpy(),
        "Rain": m15.Variables(7).ValuesAsNumpy()
    })

    # คำนวณตำแหน่งดวงอาทิตย์
    solpos = pvlib.solarposition.get_solarposition(
        time=df["date"],
        latitude=LAT,
        longitude=LON,
        altitude=2,
        temperature=25,
        pressure=1013
    )

    df["solar_altitude"] = np.maximum(0, 90 - solpos["apparent_zenith"].values)
    df["solar_azimuth"] = solpos["azimuth"].fillna(0).values

    # ฟังก์ชันคำนวณกำลังไฟฟ้า
    def solar_pv_output(DNI, DHI, GHI, T_amb, alpha, gamma_s,
                        beta=14, gamma_p=180, rho=0.2,
                        NOCT=43, gamma=-0.004,
                        N=30, P_rated=430,
                        eta_inv=0.96, f_loss=0.97):
        if alpha <= 0:
            return (DHI, T_amb, 0, 0)
        cos_ti = (math.sin(math.radians(alpha)) * math.cos(math.radians(beta)) *
                  math.cos(math.radians(gamma_s - gamma_p)) +
                  math.cos(math.radians(alpha)) * math.sin(math.radians(beta)))
        cos_ti = max(0, cos_ti)
        B_POA = DNI * cos_ti
        D_POA = DHI * (1 + math.cos(math.radians(beta))) / 2
        R_POA = rho * GHI * (1 - math.cos(math.radians(beta))) / 2
        POA = B_POA + D_POA + R_POA
        T_cell = T_amb + (NOCT - 20) / 800 * POA
        f_T = 1 + gamma * (T_cell - 25)
        P_DC = N * P_rated * (POA / 1000) * f_T
        P_AC = P_DC * eta_inv * f_loss
        return POA, T_cell, P_DC, P_AC

    # คำนวณค่า POA และกำลังไฟฟ้า
    results = [solar_pv_output(row.DNI, row.DHI, row.GHI, row.Temp,
                               row.solar_altitude, row.solar_azimuth)
               for _, row in df.iterrows()]
    df[["POA", "Tcell", "P_DC", "P_AC"]] = results

    # ปรับทศนิยมและลบ timezone
    df = df.round(2)
    df["date"] = df["date"].dt.tz_localize(None)

    return df

# -------------------------------
# 🌞 ส่วนของ Streamlit UI
# -------------------------------
st.set_page_config(page_title="Solar Dashboard Bangkok", page_icon="☀️", layout="wide")
st.title("☀️ Solar PV Forecast Dashboard – กรุงเทพมหานคร")

st.sidebar.header("⚙️ ตัวเลือก")
if st.sidebar.button("🔄 ดึงข้อมูลล่าสุด"):
    st.cache_data.clear()
    st.success("รีเฟรชข้อมูลเรียบร้อย!")

# ดึงข้อมูลจากฟังก์ชัน
df = fetch_and_calculate()

# แสดงข้อมูลตาราง
st.subheader("📋 ตัวอย่างข้อมูล (5 แถวแรก)")
st.dataframe(df.head())

# กราฟแสดงข้อมูล
st.subheader("🌡️ อุณหภูมิและความชื้น")
st.line_chart(df.set_index("date")[["Temp", "RH"]])

st.subheader("☀️ รังสีแสงอาทิตย์ (GHI, DHI, DNI, POA)")
st.line_chart(df.set_index("date")[["GHI", "DHI", "DNI", "POA"]])

st.subheader("⚡ กำลังไฟฟ้า DC / AC (W)")
st.line_chart(df.set_index("date")[["P_DC", "P_AC"]])

# พลังงานรวมรายวัน
energy_kWh = df["P_AC"].sum() * (15 / 60) / 1000  # 15-min interval
st.metric(label="พลังงานรวมต่อวัน (kWh)", value=f"{energy_kWh:.2f}")

# ปุ่มดาวน์โหลด CSV
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
csv_data = df.to_csv(index=False, encoding="utf-8-sig")
st.download_button("💾 ดาวน์โหลดข้อมูล CSV", data=csv_data,
                   file_name=f"Bangkok_Solar_{timestamp}.csv", mime="text/csv")
