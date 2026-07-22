"""E2E tests — run with the real streamlit library.

The root tests/conftest.py mocks streamlit to MagicMock so that unit and
integration tests can import project modules without a Streamlit runtime.
E2E tests need the real streamlit:

- test_app_ui.py  uses AppTest (headless) and handles mock removal internally
                 via _fix_streamlit() which deletes the MagicMock and re-imports
                 real streamlit along with all affected project modules.
- test_app_smoke.py launches a subprocess with `streamlit run` so it is
                   unaffected by the in-process mock.

No fixtures are needed — each test file handles its own setup.
"""
