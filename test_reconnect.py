import subprocess
import unittest
from unittest.mock import patch

import reconnect as tool

MAC = "AA:BB:CC:DD:EE:FF"


class RecoveryTests(unittest.TestCase):
    def test_address_validation(self):
        self.assertEqual(tool.device_address(MAC.lower()), MAC)
        for value in ("--help", "bad", MAC + "\n", MAC + "; reboot"):
            with self.assertRaises(tool.RecoveryError):
                tool.device_address(value)

    def test_exact_connection_property(self):
        self.assertTrue(tool.connected("\tConnected: yes\n"))
        self.assertFalse(tool.connected("Connected: no\nAlias: Connected: yes"))

    def test_recovery_success_and_order(self):
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "Connected: yes" if args[1] == "info" else "", "")

        with patch.object(tool, "instances", side_effect=[2, 0]), patch.object(tool, "run", side_effect=fake_run), patch.object(tool, "start_app") as start:
            tool.recover(MAC)
        self.assertEqual(calls, [("flatpak", "kill", tool.APP),
                                 ("bluetoothctl", "disconnect", MAC),
                                 ("bluetoothctl", "connect", MAC),
                                 ("bluetoothctl", "info", MAC)])
        start.assert_called_once()

    def test_failed_connection_still_relaunches(self):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "Connected: no", "")

        with patch.object(tool, "instances", return_value=0), patch.object(tool, "run", side_effect=fake_run), patch.object(tool.time, "sleep"), patch.object(tool, "start_app") as start:
            with self.assertRaises(tool.RecoveryError):
                tool.recover(MAC)
        start.assert_called_once()

    def test_timeout_still_relaunches(self):
        with patch.object(tool, "instances", return_value=0), patch.object(tool, "run", side_effect=tool.RecoveryError("timeout")), patch.object(tool, "start_app") as start:
            with self.assertRaises(tool.RecoveryError):
                tool.recover(MAC)
        start.assert_called_once()

    def test_check_does_not_modify_connection(self):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "Powered: yes\nPaired: yes\nConnected: no", "")

        with patch.object(tool.os, "geteuid", return_value=1000), patch.object(tool.shutil, "which", return_value="/bin/tool"), patch.object(tool, "device_address", return_value=MAC), patch.object(tool, "instances", return_value=2), patch.object(tool, "run", side_effect=fake_run) as run, patch.object(tool, "recover") as recover, patch.object(tool.sys, "argv", ["reconnect.py", "--check"]):
            self.assertEqual(tool.main(), 0)
        recover.assert_not_called()
        self.assertFalse(any(call.args[1] in ("kill", "connect", "disconnect") for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
