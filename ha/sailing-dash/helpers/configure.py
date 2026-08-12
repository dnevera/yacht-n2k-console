#!/usr/bin/env python3
"""Interactive CLI configuration wizard for the Sailing Dashboard."""

import argparse
import os
import sys
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_CONFIG_PATH = os.path.join(DASH_DIR, "config.yaml")
DEFAULT_TEMPLATE_PATH = os.path.join(DASH_DIR, "config.yaml.template")

CARD_NAMES = {
    "stw_gauge": "STW Gauge",
    "depth_gauge": "Depth Gauge",
    "sog_gauge": "SOG Gauge",
    "hdg_compass": "HDG Compass Card",
    "cog_compass": "COG Compass Card",
    "map": "Map Card",
    "latitude": "Latitude Tile",
    "longitude": "Longitude Tile",
    "windrose": "Windrose Card",
    "barometer_gauge": "Barometer Gauge",
    "barometer_trend": "Barometer Trend Tile",
    "glance": "Glance Summary Card",
    "chart": "Plotly Graph Chart",
    "windy_map": "Windy Map Card",
}


def load_baseline(config_path, template_path):
    config = {}
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
            # Deep merge override into baseline config
            for opt_key in (
                "chart_style",
                "arrow_spacing_hours",
                "arrow_length_scale",
                "measured_arrows_on_line",
            ):
                if opt_key in override:
                    config[opt_key] = override[opt_key]
            if "time_window" in override and isinstance(override["time_window"], dict):
                config.setdefault("time_window", {}).update(override["time_window"])
            if "sections" in override and isinstance(override["sections"], dict):
                config.setdefault("sections", {})
                for sec_key, sec_val in override["sections"].items():
                    if isinstance(sec_val, dict):
                        target_sec = config["sections"].setdefault(sec_key, {})
                        if "enabled" in sec_val:
                            target_sec["enabled"] = sec_val["enabled"]
                        if "cards" in sec_val and isinstance(sec_val["cards"], dict):
                            target_sec.setdefault("cards", {}).update(sec_val["cards"])

    return config


def prompt_bool(prompt_text, default_val):
    default_str = "Y/n" if default_val else "y/N"
    while True:
        try:
            sys.stdout.write(f"{prompt_text} [{default_str}]: ")
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:  # EOF
                return default_val
            line = line.strip().lower()
            if not line:
                return default_val
            if line in ["y", "yes", "true", "1", "д", "да"]:
                return True
            if line in ["n", "no", "false", "0", "н", "нет"]:
                return False
            print("Please enter 'y' for yes or 'n' for no.")
        except (KeyboardInterrupt, Exception):
            print("\nAborted.")
            sys.exit(1)


def prompt_choice(prompt_text, options, default_val):
    """Prompt the user to pick one of `options` (list of strings), returning the chosen value."""
    options_str = "/".join(options)
    while True:
        try:
            sys.stdout.write(f"{prompt_text} ({options_str}) [default: {default_val}]: ")
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:  # EOF
                return default_val
            line = line.strip().lower()
            if not line:
                return default_val
            if line in options:
                return line
            print(f"Please enter one of: {options_str}")
        except (KeyboardInterrupt, Exception):
            print("\nAborted.")
            sys.exit(1)


def prompt_int(prompt_text, default_val, min_val=1):
    while True:
        try:
            sys.stdout.write(f"{prompt_text} [default: {default_val}]: ")
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:  # EOF
                return default_val
            line = line.strip()
            if not line:
                return default_val
            val = int(line)
            if val < min_val:
                print(f"Value must be at least {min_val}.")
                continue
            return val
        except ValueError:
            print("Please enter a valid integer.")
        except (KeyboardInterrupt, Exception):
            print("\nAborted.")
            sys.exit(1)


def run_wizard(config, non_interactive=False):
    if non_interactive or not sys.stdin.isatty():
        print("Non-interactive mode: using baseline configuration.")
        return config

    print("\n=======================================================")
    print("  Sailing Dashboard Configuration Wizard")
    print("=======================================================\n")

    # Time Window Section
    tw = config.setdefault("time_window", {})
    hist = tw.get("history_hours", 4)
    fc = tw.get("forecast_days", 3)

    print("--- Chart Style ---")
    config["chart_style"] = prompt_choice(
        "Chart style for the wind and wave charts"
        " (open_meteo = arrow row on top of the chart)",
        ["open_meteo", "plotly"],
        config.get("chart_style", "open_meteo"),
    )
    config["arrow_spacing_hours"] = prompt_int(
        "Spacing between direction arrows on the charts (hours)",
        config.get("arrow_spacing_hours", 3),
        min_val=1,
    )
    config["arrow_length_scale"] = prompt_int(
        "Arrow length amplifier (shaft grows with wind speed / wave height)",
        config.get("arrow_length_scale", 3),
        min_val=1,
    )
    config["measured_arrows_on_line"] = prompt_bool(
        "Draw measured (NMEA) arrows on the measured value line",
        config.get("measured_arrows_on_line", True),
    )

    print("\n--- Time Windows ---")
    tw["history_hours"] = prompt_int("Measured history window on chart left of Now (hours)", hist, min_val=1)
    tw["forecast_days"] = prompt_int("Forecast window on REST query & chart right of Now (days)", fc, min_val=1)

    # Section & Card Visibility
    print("\n--- Sections & Cards Visibility ---")
    sections = config.setdefault("sections", {})
    for sec_id, sec_data in list(sections.items()):
        if not isinstance(sec_data, dict):
            continue
        sec_title = sec_id.capitalize()
        sec_enabled = sec_data.get("enabled", True)
        print(f"\nSection: [{sec_title}]")
        sec_enabled = prompt_bool(f"Enable section '{sec_title}'?", sec_enabled)
        sec_data["enabled"] = sec_enabled

        if sec_enabled:
            cards = sec_data.get("cards", {})
            for card_id, card_enabled in list(cards.items()):
                human_card_name = CARD_NAMES.get(card_id, card_id)
                cards[card_id] = prompt_bool(f"  Enable card '{human_card_name}'?", card_enabled)
            sec_data["cards"] = cards

    return config


def main():
    parser = argparse.ArgumentParser(description="Configure Sailing Dashboard parameters")
    parser.add_argument("--config-file", default=DEFAULT_CONFIG_PATH, help="Path to config.yaml")
    parser.add_argument("--template-file", default=DEFAULT_TEMPLATE_PATH, help="Path to config.yaml.template")
    parser.add_argument("--non-interactive", "--yes", "-y", action="store_true", help="Run in non-interactive mode")
    parser.add_argument("--history-hours", type=int, help="Override history time window (hours)")
    parser.add_argument("--forecast-days", type=int, help="Override forecast time window (days)")

    args = parser.parse_args()

    config = load_baseline(args.config_file, args.template_file)

    if args.history_hours is not None:
        config.setdefault("time_window", {})["history_hours"] = args.history_hours
    if args.forecast_days is not None:
        config.setdefault("time_window", {})["forecast_days"] = args.forecast_days

    config = run_wizard(config, non_interactive=args.non_interactive)

    os.makedirs(os.path.dirname(os.path.abspath(args.config_file)), exist_ok=True)
    with open(args.config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)

    print(f"\nConfiguration saved to {args.config_file}")


if __name__ == "__main__":
    main()
