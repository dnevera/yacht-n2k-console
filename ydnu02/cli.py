"""CLI module for YDNU-02 controller and monitoring tool.
"""

import sys
import argparse
from ydnu02.controller import YDNU02Controller


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for the YDNU-02 utility."""
    parser = argparse.ArgumentParser(
        description="Yacht Devices YDNU-02 USB Gateway — Controller & Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s service info           # Firmware version and serial number
  %(prog)s service shell          # Interactive REPL
  %(prog)s service reset          # Factory reset settings
  %(prog)s monitor raw -t 10      # RAW CAN monitoring for 10 seconds
  %(prog)s monitor scan           # Scan devices on the N2K bus
  %(prog)s mode auto              # Switch to AUTO mode
  %(prog)s silent off             # Disable silent mode
        """)
    parser.add_argument("-p", "--port", type=str, help="USB serial port path")
    parser.add_argument("--debug", action="store_true", help="Log raw TX/RX bytes")

    sub = parser.add_subparsers(dest="command", help="Command group")

    # --- service ---
    svc = sub.add_parser("service", help="Service Menu commands")
    svc_sub = svc.add_subparsers(dest="svc_cmd")
    svc_sub.add_parser("info", help="Welcome Screen (firmware version, serial number)")
    svc_sub.add_parser("shell", help="Interactive REPL terminal")
    svc_sub.add_parser("backup", help="Full device backup to JSON")
    svc_sub.add_parser("reset", help="RESET SETTINGS — factory-reset settings (firmware NOT affected)")
    svc_sub.add_parser("reset-filters", help="RESET FILTERS — clear all PGN filter tables")
    svc_sub.add_parser("reset-mcu", help="RESET MCU — soft reboot device")
    svc_sub.add_parser("reset-hardware", help="RESET HARDWARE — WARNING: roll back to factory firmware")
    svc_sub.add_parser("filters", help="Dump all filter tables")
    svc_sub.add_parser("settings", help="Show device settings (SET)")

    svc_help = svc_sub.add_parser("help", help="HELP [command]")
    svc_help.add_argument("help_cmd", nargs="?", default=None, help="Command to get help for")

    svc_diag = svc_sub.add_parser("diag", help="Read recorded diagnostic data")
    svc_diag.add_argument("scope", nargs="?", default="ALL",
                          choices=["ALL", "SETTINGS", "USB_RX", "USB_TX", "N2K_RX", "N2K_TX"])

    # --- monitor ---
    mon = sub.add_parser("monitor", help="NMEA data monitoring")
    mon_sub = mon.add_subparsers(dest="mon_cmd")

    mon_raw = mon_sub.add_parser("raw", help="RAW CAN frames with PGN decoding")
    mon_raw.add_argument("-t", "--time", type=float, default=10.0, help="Duration (seconds)")
    mon_raw.add_argument("--log", type=str, help="Save output to file")

    mon_0183 = mon_sub.add_parser("0183", help="NMEA 0183 sentences")
    mon_0183.add_argument("-t", "--time", type=float, default=10.0, help="Duration (seconds)")
    mon_0183.add_argument("--log", type=str, help="Save output to file")

    mon_scan = mon_sub.add_parser("scan", help="Active scan for devices on the N2K bus")
    mon_scan.add_argument("-t", "--time", type=float, default=5.0, help="Duration (seconds)")

    # --- mode ---
    md = sub.add_parser("mode", help="Set operating mode (OS Shell)")
    md.add_argument("target", choices=["auto", "0183", "raw", "n2k", "service"])

    # --- silent ---
    sl = sub.add_parser("silent", help="Silent mode on/off (OS Shell)")
    sl.add_argument("state", choices=["on", "off"])

    # --- diag-record ---
    sub.add_parser("diag-record", help="Start EEPROM diagnostic recording (OS Shell)")

    # --- firmware ---
    fw = sub.add_parser("firmware", help="Flash a .BIN firmware file to the device")
    fw.add_argument("bin_file", help="Path to firmware file (.BIN)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    ctrl = YDNU02Controller(port=args.port, debug=args.debug)

    try:
        if args.command == "service":
            if not args.svc_cmd:
                print("Specify subcommand: info, shell, backup, reset, reset-filters, reset-mcu, reset-hardware, filters, settings, help, diag")
                sys.exit(1)

            print(ctrl.enter_service_mode())

            if args.svc_cmd == "info":
                pass
            elif args.svc_cmd == "shell":
                ctrl.service_interactive()
            elif args.svc_cmd == "backup":
                ctrl.service_backup()
            elif args.svc_cmd == "help":
                print(ctrl.service_help(args.help_cmd))
            elif args.svc_cmd == "reset":
                print(ctrl.service_reset_settings())
            elif args.svc_cmd == "reset-filters":
                print(ctrl.service_reset_filters())
            elif args.svc_cmd == "reset-mcu":
                print("RESET MCU — rebooting device...")
                print(ctrl.service_reset_mcu())
            elif args.svc_cmd == "reset-hardware":
                print("\nRESET HARDWARE — ROLLING BACK TO FACTORY FIRMWARE")
                print("  This will restore the factory firmware stored in EEPROM.")
                print("  All settings will be reset.\n")
                print("[SAFETY] Auto-backup before reset-hardware...")
                backup_path = ctrl.service_backup()
                print(f"[SAFETY] Backup saved: {backup_path}\n")
                confirm = input("Type 'RESET' to confirm: ").strip()
                if confirm == "RESET":
                    print(ctrl.service_reset_hardware())
                    print("[YDNU02] Rolling back to factory firmware...")
                    print("[YDNU02] Wait for LED signals and reconnect.")
                else:
                    print("[CANCELLED] Reset hardware aborted.")
            elif args.svc_cmd == "diag":
                print(ctrl.service_diag(args.scope))
            elif args.svc_cmd == "filters":
                for filt in ["GLOBAL_RX", "GLOBAL_TX", "RAW_RX", "RAW_TX",
                             "N2K_RX", "N2K_TX", "0183_RX", "0183_TX"]:
                    print(f"\n--- {filt} ---")
                    print(ctrl.service_print_filter(filt))
            elif args.svc_cmd == "settings":
                print(ctrl.service_set())

            if args.svc_cmd not in ("reset-hardware", "reset-mcu"):
                ctrl.exit_service_mode("AUTO")

        elif args.command == "monitor":
            if not args.mon_cmd:
                print("Specify subcommand: raw, 0183, scan")
                sys.exit(1)

            if args.mon_cmd == "raw":
                ctrl.monitor_raw(duration=args.time, log_file=args.log)
            elif args.mon_cmd == "0183":
                ctrl.monitor_0183(duration=args.time, log_file=args.log)
            elif args.mon_cmd == "scan":
                ctrl.scan_bus(duration=args.time)

        elif args.command == "mode":
            ctrl.set_mode(args.target)

        elif args.command == "silent":
            ctrl.set_silent(args.state == "on")

        elif args.command == "diag-record":
            ctrl.start_diag_record()

        elif args.command == "firmware":
            ctrl.update_firmware(args.bin_file)

    except KeyboardInterrupt:
        print("\n[Interrupted]")
    finally:
        ctrl._close_terminal()
        print("[YDNU02] Done.")


if __name__ == "__main__":
    main()
