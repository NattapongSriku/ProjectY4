import streamlit as st
import requests

ESP32_IP = "192.168.1.92"   # IP ของ ESP32

st.title("AC Control")

if st.button("AC ON"):
    requests.get(f"http://192.168.1.92/on")
    st.success("AC ON sent")

if st.button("AC OFF"):
    requests.get(f"http://192.168.1.92/off")

    st.success("AC OFF sent")
