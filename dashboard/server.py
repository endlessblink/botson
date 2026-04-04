"""Run the dashboard server standalone or alongside the bot."""

import os
import uvicorn


def run_dashboard(host: str = "0.0.0.0", port: int = 8080):
    """Run the dashboard server."""
    uvicorn.run(
        "dashboard.app:app",
        host=host,
        port=int(os.getenv("DASHBOARD_PORT", str(port))),
        reload=False,
    )


if __name__ == "__main__":
    run_dashboard()
