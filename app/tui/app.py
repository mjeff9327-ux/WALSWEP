def run_tui(scan_engine=None, event_bus=None, solver=None, config=None):
    from textual_web import run_textual_ui
    run_textual_ui(scan_engine, event_bus, config, solver)
