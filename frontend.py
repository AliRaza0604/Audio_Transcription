import time
import requests
import streamlit as st

API = "http://127.0.0.1:8000"

st.title("Audio Transcription")

file = st.file_uploader("Upload audio", type=["mp3", "wav", "m4a", "flac", "aac"])

if file and st.button("Transcribe"):
    with st.spinner("Uploading..."):
        r = requests.post(f"{API}/transcribe", files={"file": (file.name, file, file.type)})
        r.raise_for_status()
        job_id = r.json()["job_id"]

    st.info(f"Job ID: `{job_id}`")

    with st.spinner("Processing..."):
        while True:
            status = requests.get(f"{API}/status/{job_id}").json()["status"]
            if status == "completed":
                break
            if status == "failed":
                st.error("Transcription failed.")
                st.stop()
            time.sleep(2)

    result = requests.get(f"{API}/result/{job_id}").json()["result"]
    st.success(f"Done — Language: **{result['language']}** | Duration: **{result['duration']}s**")

    for seg in result["segments"]:
        st.write(f"`{seg['start']}s → {seg['end']}s` {seg['text']}")
