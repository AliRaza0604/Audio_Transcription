# ============================================================
# Test Script for Audio Transcription API
# ============================================================

import requests

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

API_URL = "http://127.0.0.1:8000/transcribe"

AUDIO_FILE_PATH = "lj001-0001_qvtlE4sD.wav"   # change to your audio file

# ------------------------------------------------------------
# SEND REQUEST
# ------------------------------------------------------------

with open(AUDIO_FILE_PATH, "rb") as audio_file:

    files = {
        "file": audio_file
    }

    response = requests.post(
        API_URL,
        files=files
    )

# ------------------------------------------------------------
# PRINT RESPONSE
# ------------------------------------------------------------

print("Status Code:", response.status_code)
print()
print("Response JSON:")
print(response.json())