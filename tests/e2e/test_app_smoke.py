"""通过 subprocess 验证应用可启动"""
import subprocess
import time
import pytest


def test_app_launches():
    proc = subprocess.Popen(
        ["streamlit", "run", "filter_app/streamlit_app.py", "--server.headless=true"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    time.sleep(10)
    # 检查进程未崩溃
    assert proc.poll() is None, f"streamlit process exited prematurely, rc={proc.returncode}"
    proc.terminate()
